from pydantic import BaseModel, Field


class MenuItemOut(BaseModel):
    id: str
    label: str
    enabled: bool
    checked: bool | None
    children: list["MenuItemOut"] = Field(default_factory=list)


class MenuResponse(BaseModel):
    items: list[MenuItemOut]
