from enum import Enum


class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'


class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'


class BatchStatus(str, Enum):
    pending = 'pending'      # 待配：尚未配齐
    allocating = 'allocating'  # 配餐计算中；中断后按原批恢复
    short = 'short'          # 待补：有份数配不齐，整批不可下发
    ready = 'ready'          # 配齐待下发
    dispatched = 'dispatched'  # 已下发：全部份数均有标签


class MealStatus(str, Enum):
    pending = 'pending'        # 待分配
    allocated = 'allocated'    # 已配齐待下发
    held = 'held'              # 挂起待重配（原标签保留在历史）
    dispatched = 'dispatched'  # 已下发未出餐
    produced = 'produced'      # 已出餐
