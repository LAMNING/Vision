class AppError(Exception):
    '''业务异常'''


class NotFoundError(AppError):
    '''资源不存在（或已软删）'''


class ConflictError(AppError):
    '''唯一性 / 状态冲突'''


class PermissionDeniedError(AppError):
    '''无权限'''


class ValidationError(AppError):
    '''参数或前置条件不满足'''


# ---- Group ----
class GroupError(AppError): ...
class GroupNotFoundError(NotFoundError, GroupError):
    def __init__(self, group_id: int):
        super().__init__(f'group {group_id} not found')
        self.group_id = group_id

class GroupNotEmptyError(ConflictError, GroupError):
    def __init__(self, group_id: int):
        super().__init__(f'group {group_id} still has active projects')
        self.group_id = group_id

class GroupHasChildrenError(ConflictError, GroupError):
    def __init__(self, group_id: int):
        super().__init__(f'group {group_id} still has child groups')
        self.group_id = group_id

class PersonalGroupImmutableError(ValidationError, GroupError):
    pass

class InvalidGroupTypeError(ValidationError, GroupError):
    pass


# ---- Member ----
class MemberError(AppError): ...
class MemberNotFoundError(NotFoundError, MemberError):
    def __init__(self, account_id: int):
        super().__init__(f'membership of account {account_id} not found')
        self.account_id = account_id

class MemberAlreadyExistsError(ConflictError, MemberError):
    pass

class CannotRemoveOwnerError(ConflictError, MemberError):
    pass

class InvalidRoleError(ValidationError, MemberError):
    pass

class OwnerTransferRequiredError(ConflictError, MemberError):
    pass


# ---- Project ----
class ProjectError(AppError): ...
class ProjectNotFoundError(NotFoundError, ProjectError):
    def __init__(self, project_id: int):
        super().__init__(f'project {project_id} not found')
        self.project_id = project_id

class ProjectArchivedError(ConflictError, ProjectError):
    def __init__(self, project_id: int):
        super().__init__(f'project {project_id} is archived')
        self.project_id = project_id

class ProjectNameConflictError(ConflictError, ProjectError):
    def __init__(self, name: str):
        super().__init__(f'project name {name!r} already exists in this group')
        self.name = name

class ProjectMemberNotFoundError(NotFoundError, ProjectError):
    def __init__(self, project_id: int, account_id: int):
        super().__init__(
            f'membership of account {account_id} in project {project_id} not found'
        )
        self.project_id = project_id
        self.account_id = account_id

class InvalidProjectRoleError(ValidationError, ProjectError):
    pass


# ---- Project Category ----
class ProjectCategoryError(AppError): ...
class CategoryNotFoundError(NotFoundError, ProjectCategoryError):
    def __init__(self, category_id: int):
        super().__init__(f'category {category_id} not found')
        self.category_id = category_id

class CategoryNameConflictError(ConflictError, ProjectCategoryError):
    def __init__(self, name: str):
        super().__init__(f'category name {name!r} already exists in this project')
        self.name = name

class InvalidCategoryError(ValidationError, ProjectCategoryError):
    pass


# ---- Project Auto Config ----
class AutoConfigError(AppError): ...
class InvalidAutoConfigError(ValidationError, AutoConfigError):
    pass


# ---- Invitation ----
class InvitationError(AppError): ...
class InvitationNotFoundError(NotFoundError, InvitationError):
    pass

class InvitationNotPendingError(ConflictError, InvitationError):
    def __init__(self, status: str):
        super().__init__(f"invitation is '{status}', not pending")
        self.status = status

class InvitationExpiredError(ConflictError, InvitationError):
    pass

class InvitationScopeError(ValidationError, InvitationError):
    def __init__(self, scope: str):
        super().__init__(f'invalid invitation scope: {scope}')
        self.scope = scope

class DuplicateInvitationError(ConflictError, InvitationError):
    def __init__(self, message: str = '已有待处理的邀请，或对方已经是成员'):
        super().__init__(message)


# ---- Enterprise ----
class EnterpriseError(AppError): ...
class EnterpriseNotFoundError(NotFoundError, EnterpriseError):
    '''企业不存在，或当前账号不是成员（不区分，避免泄露企业是否存在）。'''
    def __init__(self, enterprise_id: int):
        super().__init__(f'enterprise {enterprise_id} not found')
        self.enterprise_id = enterprise_id

class EnterpriseMemberNotFoundError(NotFoundError, EnterpriseError):
    def __init__(self, account_id: int):
        super().__init__(f'account {account_id} is not a member of this enterprise')
        self.account_id = account_id

class EnterpriseQuotaError(ConflictError, EnterpriseError):
    '''配额超限；detail 直接给用户看（前端原样显示）。'''
    def __init__(self, resource: str, limit: int, used: int):
        super().__init__(f'{resource}已满：{used}/{limit}')
        self.resource = resource
        self.limit = limit
        self.used = used

class EnterpriseRoleError(PermissionDeniedError, EnterpriseError):
    def __init__(self, need: str):
        super().__init__(f'需要{need}及以上权限')
        self.need = need


# ---- Image ----
class ImageError(AppError): ...
class ImageNotFoundError(NotFoundError, ImageError):
    def __init__(self, image_id: int):
        super().__init__(f'image {image_id} not found')
        self.image_id = image_id

class ImageDeletedError(ConflictError, ImageError):
    def __init__(self, image_id: int):
        super().__init__(f'image {image_id} is soft-deleted')
        self.image_id = image_id

class ImageBlobMissingError(NotFoundError, ImageError):
    '''库里有记录、存储里没有对象：文件被外部清理或迁移丢失。'''
    def __init__(self, image_id: int):
        super().__init__(f'image {image_id} blob missing in storage')
        self.image_id = image_id

class ProjectImageNotFoundError(NotFoundError, ImageError):
    '''图片存在，但没有被加进这个项目。'''
    def __init__(self, project_id: int, image_id: int):
        super().__init__(
            f'project image {image_id} not found in project {project_id}'
        )
        self.project_id = project_id
        self.image_id = image_id

class ImageCrossGroupError(ValidationError, ImageError):
    '''图片与任务不在同一组：会打乱额度归属，当前不支持跨组用图'''
    def __init__(self, image_id: int, image_group_id: int, task_group_id: int):
        super().__init__(
            f'image {image_id} belongs to group {image_group_id}, '
            f'task belongs to group {task_group_id}'
        )
        self.image_id = image_id
        self.image_group_id = image_group_id
        self.task_group_id = task_group_id

class QuotaExceededError(ConflictError, ImageError):
    def __init__(self, resource: str, limit: int, used: int):
        super().__init__(f'{resource} quota exceeded: {used}/{limit}')
        self.resource = resource
        self.limit = limit
        self.used = used


# ---- Task ----
class TaskError(AppError): ...
class TaskNotFoundError(NotFoundError, TaskError):
    def __init__(self, task_id: int):
        super().__init__(f'task {task_id} not found')
        self.task_id = task_id

class InvalidTaskTransitionError(ConflictError, TaskError):
    def __init__(self, from_status: str, to_status: str):
        super().__init__(f'illegal task transition: {from_status} -> {to_status}')
        self.from_status = from_status
        self.to_status = to_status

class TaskConcurrencyError(ConflictError, TaskError):
    '''乐观锁冲突：version 已被其他请求推进'''
    pass

class TaskRetryExhaustedError(ConflictError, TaskError):
    pass

class TaskAlreadyFinalError(ConflictError, TaskError):
    pass

class NoReviewerAvailableError(ValidationError, TaskError):
    '''Project.default_reviewer='project_manager' 但项目里没有 manager'''
    pass

class SubmitterNotAllowedError(PermissionDeniedError, TaskError):
    pass

class ReviewerNotAllowedError(PermissionDeniedError, TaskError):
    pass


# ---- TaskImage ----
class TaskImageError(AppError): ...
class TaskImageNotFoundError(NotFoundError, TaskImageError):
    def __init__(self, task_id: int, image_id: int):
        super().__init__(f'image {image_id} not in task {task_id}')
        self.task_id = task_id
        self.image_id = image_id

class InvalidImageReviewError(ValidationError, TaskImageError):
    pass


# ---- Annotation ----
class AnnotationError(AppError): ...
class InvalidAnnotationError(ValidationError, AnnotationError):
    pass


# ---- Review ----
class ReviewError(AppError): ...
class InvalidReviewDecisionError(ValidationError, ReviewError):
    pass

class IncompleteReviewError(ValidationError, ReviewError):
    '''approve 要求全部图片 approved；reject 要求至少一张 rejected'''
    pass

class ReviewRoundConflictError(ConflictError, ReviewError):
    pass

class ReviewRequiredError(ConflictError, ReviewError):
    '''迁移到 approved/rejected_* 必须先有对应结论的 Review（禁止绕过复检）'''
    def __init__(self, task_id: int, to_status: str):
        super().__init__(
            f'task {task_id} cannot move to {to_status!r} without a matching '
            f'review; use submit_review()'
        )
        self.task_id = task_id
        self.to_status = to_status


# ---- Platform Admin ----
class AdminError(AppError): ...

class PlatformAdminRequiredError(PermissionDeniedError, AdminError):
    def __init__(self):
        super().__init__('需要平台管理员权限')

class AccountNotFoundError(NotFoundError, AdminError):
    def __init__(self, account_id: int):
        super().__init__(f'account {account_id} not found')
        self.account_id = account_id


# ---- Storage browsing（/storage，平台管理员） ----
class StoragePathNotFoundError(NotFoundError):
    def __init__(self, path: str):
        super().__init__(f'storage path {path!r} not found')
        self.path = path


# ---- 限流 ----
class TooManyRequestsError(AppError):
    '''触发限流：带 retry_after 秒数，路由层回 429 + Retry-After。'''

    def __init__(self, detail: str, retry_after: int = 60):
        super().__init__(detail)
        self.retry_after = max(1, int(retry_after))


# HTTP 映射（路由层用）
#
#   NotFoundError           -> 404
#   ConflictError           -> 409
#   PermissionDeniedError   -> 403
#   ValidationError         -> 400
#   TooManyRequestsError    -> 429
#   AppError（其余）         -> 500