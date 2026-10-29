from pydantic import BaseModel

__all__ = ['CategoryStat', 'DayStat', 'ProjectStatsRead']


class CategoryStat(BaseModel):
    category_id: int | None = None
    name: str
    count: int


class DayStat(BaseModel):
    day: str
    count: int


class ProjectStatsRead(BaseModel):
    project_id: int
    tasks_total: int
    tasks_by_status: dict[str, int]
    tasks_by_mode: dict[str, int]
    images_total: int
    annotations_current: int
    annotations_by_source: dict[str, int]
    annotations_by_category: list[CategoryStat]
    annotations_by_day: list[DayStat]
    approved_tasks: int
    reviewed_tasks: int
    review_pass_rate: float
