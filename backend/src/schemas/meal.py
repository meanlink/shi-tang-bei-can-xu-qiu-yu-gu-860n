from pydantic import BaseModel, Field


class DishInput(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=120)
    allergens: list[str] = Field(default_factory=list)
    allergen_version: str = Field(default='', max_length=40)
    label_status: str = Field(default='known', max_length=20)
    default_portions: int = Field(default=0, ge=0, le=5000)


class DishPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    allergens: list[str] | None = None
    allergen_version: str | None = Field(default=None, max_length=40)
    label_status: str | None = Field(default=None, max_length=20)
    default_portions: int | None = Field(default=None, ge=0, le=5000)
    active: bool | None = None


class BatchInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    job_id: int
    date: str = Field(min_length=10, max_length=10)
    allergen_version: str = Field(default='', max_length=40)
    note: str = Field(default='', max_length=500)


class ComboInput(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    exclusions: list[str] = Field(default_factory=list)
    count: int = Field(ge=1, le=5000)


class BatchDishInput(BaseModel):
    dish_id: int
    portions: int = Field(ge=0, le=5000)


class ConfigureInput(BaseModel):
    expected_version: int | None = None
    allergen_version: str | None = Field(default=None, max_length=40)
    dishes: list[BatchDishInput] = Field(default_factory=list)
    combos: list[ComboInput] = Field(default_factory=list)


class AllocateInput(BaseModel):
    expected_version: int | None = None


class SubstitutionInput(BaseModel):
    from_dish_id: int
    to_dish_id: int
    count: int = Field(ge=1, le=5000)
    reason: str = Field(min_length=2, max_length=500)
    expected_version: int | None = None


class DispatchInput(BaseModel):
    expected_version: int | None = None
