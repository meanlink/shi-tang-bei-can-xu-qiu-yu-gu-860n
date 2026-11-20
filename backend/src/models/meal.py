from sqlalchemy import String, Double, JSON, Integer, ForeignKey, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Dish(Base):
    """菜品主数据：过敏原标签、标签版本与默认可用份数。"""

    __tablename__ = 'dishes'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    allergens: Mapped[list] = mapped_column(JSON, default=list)
    allergen_version: Mapped[str] = mapped_column(String(40))
    label_status: Mapped[str] = mapped_column(String(20), default='known')
    default_portions: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Double)


class MealBatch(Base):
    """按一张已批准每日份数建立的过敏餐配餐批次。"""

    __tablename__ = 'meal_batches'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    date: Mapped[str] = mapped_column(String(10))
    total: Mapped[int] = mapped_column(Integer)
    plan_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    allergen_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default='pending')
    version: Mapped[int] = mapped_column(Integer, default=1)
    resume_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    shortage: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(500), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)
    dispatched_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class BatchCombo(Base):
    """批次登记的忌口组合及需要份数；未登记部分按无特殊忌口处理。"""

    __tablename__ = 'batch_combos'
    __table_args__ = (UniqueConstraint('batch_id', 'name', name='uq_batch_combo_name'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('meal_batches.id'), index=True)
    name: Mapped[str] = mapped_column(String(80))
    exclusions: Mapped[list] = mapped_column(JSON, default=list)
    count: Mapped[int] = mapped_column(Integer, default=0)


class BatchDish(Base):
    """批次登记的菜品份数；容量只读登记，不随重试递减。"""

    __tablename__ = 'batch_dishes'
    __table_args__ = (UniqueConstraint('batch_id', 'dish_id', name='uq_batch_dish'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('meal_batches.id'), index=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey('dishes.id'))
    portions: Mapped[int] = mapped_column(Integer, default=0)


class Meal(Base):
    """每一份餐的配餐结果与用料依据快照。"""

    __tablename__ = 'meals'
    __table_args__ = (
        UniqueConstraint('batch_id', 'seq', name='uq_meal_batch_seq'),
        Index('ix_meals_batch_status', 'batch_id', 'status'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('meal_batches.id'), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    combo_id: Mapped[int | None] = mapped_column(ForeignKey('batch_combos.id'), nullable=True)
    dish_id: Mapped[int | None] = mapped_column(ForeignKey('dishes.id'), nullable=True)
    preferred_dish_id: Mapped[int | None] = mapped_column(ForeignKey('dishes.id'), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    label_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    label_version: Mapped[str] = mapped_column(String(40), default='')
    basis: Mapped[dict] = mapped_column(JSON, default=dict)
    dispatch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label_history: Mapped[list] = mapped_column(JSON, default=list)
    produced_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    updated_at: Mapped[float] = mapped_column(Double)


class Substitution(Base):
    """替料登记：把某菜品最多 N 份替换为另一菜品，并挂起受影响未出餐份。"""

    __tablename__ = 'substitutions'
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('meal_batches.id'), index=True)
    from_dish_id: Mapped[int] = mapped_column(ForeignKey('dishes.id'))
    to_dish_id: Mapped[int] = mapped_column(ForeignKey('dishes.id'))
    count: Mapped[int] = mapped_column(Integer)
    applied_count: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(500), default='')
    actor: Mapped[str] = mapped_column(String(150))
    affected: Mapped[list] = mapped_column(JSON, default=list)
    version_after: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Double)


class MealDispatch(Base):
    """一次整批下发生成的不可变标签快照。"""

    __tablename__ = 'meal_dispatches'
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('meal_batches.id'), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    total: Mapped[int] = mapped_column(Integer)
    allergen_version: Mapped[str] = mapped_column(String(40))
    checksum: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(180))
    created_at: Mapped[float] = mapped_column(Double)
