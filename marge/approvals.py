import json
import logging as log

from . import gitlab

GET, POST, PUT = gitlab.GET, gitlab.POST, gitlab.PUT


class Approvals(gitlab.Resource):
    """Approval info for a MergeRequest."""

    def refetch_info(self):
        gitlab_version = self._api.version()
        if gitlab_version.release >= (9, 2, 2):
            approver_url = '/projects/{0.project_id}/merge_requests/{0.iid}/approvals'.format(self)
        else:
            # GitLab botched the v4 api before 9.2.3
            approver_url = '/projects/{0.project_id}/merge_requests/{0.id}/approvals'.format(self)

        if gitlab_version.release >= (13, 2, 0):
            # 优先查找project设置的approvers
            gitlab_variables=self._api.call(GET('/projects/{0.project_id}/variables'.format(self)))
            mr_approvers = next((var['value'] for var in gitlab_variables if var.get('key') == 'MR_APPROVERS'), '')

            # project没有设置approvers时查找其上级group，层级关系越近优先级越高
            # 如RD-CFE/RD-CFE-CODE/PTC设置的approvers优先级高于RD-CFE/RD-CFE-CODE
            if mr_approvers:
                log.info('Use merge request approvers from project' )
            else:
                parent_groups=self._api.call(GET('/projects/{0.project_id}/groups'.format(self)))
                parent_groups.sort(key=lambda x: len(x["full_path"]), reverse=True)
                for group in parent_groups:
                    gitlab_variables=self._api.call(GET('/groups/{0}/variables'.format(group['id'])))
                    mr_approvers = next((var['value'] for var in gitlab_variables if var.get('key') == 'MR_APPROVERS'), '')
                    if mr_approvers:
                        log.info('Use merge request approvers from parent group: ' + group['web_url'])
                        break
        else:
            mr_approvers=''

        if mr_approvers:
            mr_approvers_list=mr_approvers.split(",")

            approved_by_user_list = []
            self._info = self._api.call(GET(approver_url))
            for approve_info in self._info['approved_by']:
                approved_by_user_list.append(approve_info['user']['username'])

            log.info('Merge request approvers: ' + ', '.join(mr_approvers_list))
            log.info('Approve users: ' + ', '.join(approved_by_user_list))

            # mr_approvers_list和approved_by_user_list用户有交集时允许mr合入
            common_user = set(mr_approvers_list) & set(approved_by_user_list)
            if len(common_user) == 0:
                self._info = dict(self._info, approvals_left=1, approved_by=[])
                log.info('No common approvers match')
            else:
                self._info = dict(self._info, approvals_left=0, approved_by=[])
                log.info('Merge request is approved by: ' + str(common_user))
        else:
            # Approvals are in CE since 13.2
            if gitlab_version.is_ee or gitlab_version.release >= (13, 2, 0):
                self._info = self._api.call(GET(approver_url))
            else:
                self._info = dict(self._info, approvals_left=0, approved_by=[])

        # test: always skip merge
        # self._info = dict(self._info, approvals_left=5, approved_by=[])

    @property
    def iid(self):
        return self.info['iid']

    @property
    def project_id(self):
        return self.info['project_id']

    @property
    def approvals_left(self):
        return self.info.get("approvals_left", 0) or 0

    @property
    def sufficient(self):
        return not self.approvals_left

    @property
    def approver_usernames(self):
        return [who['user']['username'] for who in self.info['approved_by']]

    @property
    def approver_ids(self):
        """Return the uids of the approvers."""
        return [who['user']['id'] for who in self.info['approved_by']]

    def reapprove(self):
        """Impersonates the approvers and re-approves the merge_request as them.

        The idea is that we want to get the approvers, push the rebased branch
        (which may invalidate approvals, depending on GitLab settings) and then
        restore the approval status.
        """
        self.approve(self)

    def approve(self, obj):
        """Approve an object which can be a merge_request or an approval."""
        if self._api.version().release >= (9, 2, 2):
            approve_url = '/projects/{0.project_id}/merge_requests/{0.iid}/approve'.format(obj)
        else:
            # GitLab botched the v4 api before 9.2.3
            approve_url = '/projects/{0.project_id}/merge_requests/{0.id}/approve'.format(obj)

        for uid in self.approver_ids:
            self._api.call(POST(approve_url), sudo=uid)
