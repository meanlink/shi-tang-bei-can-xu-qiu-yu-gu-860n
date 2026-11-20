"""过敏餐配餐：忌口组合登记、份数匹配、替料与整批下发。

关键规则：
- 份数只来自已批准分析结果的每日建议备餐量；批次一经建立即固定总数。
- 菜品份数是登记容量，只会被“替料”显式调整；每次重算都从登记容量
  重新分配，所以中断重试不会重复占用。
- 标签不明、过敏原版本不一致或已停用的菜品暂停分配。
- 先求最大可配份数：能全部配齐才生成标签；否则整批进入待补，不下发。
- 下发核对批次版本与每份餐的标签依据；替料后旧版本一律拒绝。
- 已下发份保留原标签；替料影响到的未出餐份挂起，重新配餐后另发新单。
"""
import csv
import hashlib
import json
import time
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import select

from src.constants.allergens import ALLERGEN_LABELS, DEFAULT_ALLERGEN_VERSION, label_list
from src.models import (
    BatchCombo,
    BatchDish,
    Dish,
    Job,
    Meal,
    MealBatch,
    MealDispatch,
    Substitution,
)
from src.services.audit import record


# ---------------------------------------------------------------- helpers

def now():
    return time.time()


def label_hash(dish):
    payload = {
        'allergens': sorted(dish.allergens or []),
        'version': dish.allergen_version,
        'status': dish.label_status,
        'name': dish.name,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def dish_dict(d):
    return {
        'id': d.id,
        'code': d.code,
        'name': d.name,
        'allergens': d.allergens or [],
        'allergen_labels': label_list(d.allergens),
        'allergen_version': d.allergen_version,
        'label_status': d.label_status,
        'label_hash': label_hash(d),
        'default_portions': d.default_portions,
        'active': d.active,
    }


def combo_dict(c):
    return {
        'id': c.id,
        'name': c.name,
        'exclusions': c.exclusions or [],
        'exclusion_labels': label_list(c.exclusions),
        'count': c.count,
    }


def meal_dict(m, dish_map, combo_map):
    d = dish_map.get(m.dish_id)
    c = combo_map.get(m.combo_id)
    return {
        'id': m.id,
        'seq': m.seq,
        'combo_id': m.combo_id,
        'combo_name': c.name if c else '',
        'dish_id': m.dish_id,
        'dish_code': d.code if d else None,
        'dish_name': d.name if d else None,
        'status': m.status,
        'label_version': m.label_version,
        'preferred_dish_id': m.preferred_dish_id,
        'dispatch_id': m.dispatch_id,
        'basis': m.basis or {},
        'label_history': m.label_history or [],
        'updated_at': m.updated_at,
    }


def batch_dict(b, db=None, detail=False):
    out = {
        'id': b.id,
        'name': b.name,
        'job_id': b.job_id,
        'date': b.date,
        'total': b.total,
        'allergen_version': b.allergen_version,
        'status': b.status,
        'version': b.version,
        'shortage': b.shortage or {},
        'note': b.note,
        'created_at': b.created_at,
        'updated_at': b.updated_at,
        'dispatched_at': b.dispatched_at,
    }
    if not detail or db is None:
        return out
    dishes = {d.id: d for d in db.scalars(select(Dish))}
    combos = list(db.scalars(select(BatchCombo).where(BatchCombo.batch_id == b.id).order_by(BatchCombo.id)))
    combo_map = {c.id: c for c in combos}
    rows = list(db.scalars(select(Meal).where(Meal.batch_id == b.id).order_by(Meal.seq)))
    portions = list(db.scalars(select(BatchDish).where(BatchDish.batch_id == b.id).order_by(BatchDish.id)))
    dispatched = list(db.scalars(select(Meal).where(Meal.batch_id == b.id, Meal.status == 'dispatched')))
    produced = list(db.scalars(select(Meal).where(Meal.batch_id == b.id, Meal.status == 'produced')))
    dispatch_rows = list(db.scalars(select(MealDispatch).where(MealDispatch.batch_id == b.id).order_by(MealDispatch.id.desc())))
    subs = list(db.scalars(select(Substitution).where(Substitution.batch_id == b.id).order_by(Substitution.id.desc())))
    counts = defaultdict(int)
    for m in rows:
        counts[m.status] += 1
    out.update({
        'dishes': [
            {
                'dish_id': p.dish_id,
                'portions': p.portions,
                'dish': dish_dict(dishes[p.dish_id]) if p.dish_id in dishes else None,
            }
            for p in portions
        ],
        'combos': [combo_dict(c) for c in combos],
        'meals': [meal_dict(m, dishes, combo_map) for m in rows],
        'meal_counts': dict(counts),
        'dispatches': [dispatch_dict(x) for x in dispatch_rows],
        'substitutions': [substitution_dict(s, dishes) for s in subs],
        'progress': {
            'dispatched_meals': len(dispatched),
            'produced_meals': len(produced),
            'pending_meals': counts.get('pending', 0) + counts.get('held', 0),
        },
    })
    return out


def dispatch_dict(x):
    return {
        'id': x.id,
        'batch_id': x.batch_id,
        'total': x.total,
        'allergen_version': x.allergen_version,
        'checksum': x.checksum,
        'filename': x.filename,
        'created_at': x.created_at,
    }


def substitution_dict(s, dishes):
    frm, to = dishes.get(s.from_dish_id), dishes.get(s.to_dish_id)
    return {
        'id': s.id,
        'from_dish_id': s.from_dish_id,
        'from_dish': frm.name if frm else '',
        'to_dish_id': s.to_dish_id,
        'to_dish': to.name if to else '',
        'count': s.count,
        'applied_count': s.applied_count,
        'reason': s.reason,
        'affected': s.affected or [],
        'version_after': s.version_after,
        'actor': s.actor,
        'created_at': s.created_at,
    }


def create_dish(db, data):
    """菜品主数据：过敏原目录内的标签、版本与标签状态。"""
    code = data.code.strip().upper()
    if db.scalar(select(Dish).where(Dish.code == code)):
        raise HTTPException(409, '菜品编码已存在')
    allergens = [str(c) for c in data.allergens]
    if len(allergens) != len(set(allergens)) or any(c not in ALLERGEN_LABELS for c in allergens):
        raise HTTPException(422, '过敏原代码不在目录内或重复')
    if data.label_status not in ('known', 'unknown'):
        raise HTTPException(422, '标签状态只能是 known 或 unknown')
    if data.label_status == 'unknown' and allergens:
        raise HTTPException(422, '标签不明的菜品不能同时登记过敏原')
    d = Dish(
        code=code,
        name=data.name.strip(),
        allergens=allergens,
        allergen_version=(data.allergen_version or DEFAULT_ALLERGEN_VERSION).strip(),
        label_status=data.label_status,
        default_portions=max(0, int(data.default_portions)),
        active=True,
        created_at=now(),
    )
    db.add(d)
    db.flush()
    return d


def update_dish(db, dish, data):
    if data.name is not None:
        if len(data.name.strip()) < 2:
            raise HTTPException(422, '菜品名称至少两个字符')
        dish.name = data.name.strip()
    if data.allergens is not None:
        allergens = [str(c) for c in data.allergens]
        if len(allergens) != len(set(allergens)) or any(c not in ALLERGEN_LABELS for c in allergens):
            raise HTTPException(422, '过敏原代码不在目录内或重复')
        dish.allergens = allergens
    if data.allergen_version is not None:
        if not data.allergen_version.strip():
            raise HTTPException(422, '过敏原版本不能为空')
        dish.allergen_version = data.allergen_version.strip()
    if data.label_status is not None:
        if data.label_status not in ('known', 'unknown'):
            raise HTTPException(422, '标签状态只能是 known 或 unknown')
        dish.label_status = data.label_status
    if data.default_portions is not None:
        if data.default_portions < 0:
            raise HTTPException(422, '默认份数不能为负')
        dish.default_portions = int(data.default_portions)
    if data.active is not None:
        dish.active = bool(data.active)
    if dish.label_status == 'unknown':
        dish.allergens = []
    db.flush()
    return dish


# ---------------------------------------------------------------- batch lifecycle

def create_batch(db, data, user):
    job = db.get(Job, data.job_id)
    if not job or job.status != 'completed' or not job.approved:
        raise HTTPException(409, '请选择已完成且获批准的每日份数分析')
    rows = (job.result or {}).get('table', {}).get('rows', [])
    match = next((r for r in rows if str(r.get('date')) == data.date), None)
    if not match:
        raise HTTPException(422, '该分析结果中没有这一天的建议备餐份数')
    try:
        total = int(match.get('plan'))
    except (TypeError, ValueError):
        raise HTTPException(422, '该日建议份数无效')
    if total <= 0:
        raise HTTPException(422, '该日建议份数必须大于 0')
    if total > 5000:
        raise HTTPException(422, '单日份数超过 5,000，请拆分批次')
    version = (data.allergen_version or DEFAULT_ALLERGEN_VERSION).strip()
    if len(version) < 1:
        raise HTTPException(422, '过敏原版本不能为空')
    b = MealBatch(
        name=data.name.strip(),
        job_id=job.id,
        date=data.date,
        total=total,
        plan_snapshot={'date': data.date, 'plan': total, 'forecast': match.get('forecast'), 'buffer': match.get('buffer'), 'job_name': job.name, 'params': job.params},
        allergen_version=version,
        status='pending',
        version=1,
        resume_status=None,
        shortage={},
        note=data.note.strip() if data.note else '',
        created_by=user.id,
        created_at=now(),
        updated_at=now(),
    )
    db.add(b)
    db.flush()
    for seq in range(1, total + 1):
        db.add(Meal(batch_id=b.id, seq=seq, status='pending', label_snapshot={}, label_version='', basis={}, label_history=[], updated_at=now()))
    db.flush()
    return b


def configure_batch(db, batch, data, user):
    if batch.status in ('dispatched', 'allocating'):
        raise HTTPException(409, '已下发或配餐中的批次不能用登记修改；请先完成/恢复，或改用替料')
    if batch.dispatched_at or db.scalar(select(Meal.id).where(Meal.batch_id == batch.id, Meal.status.in_(('dispatched', 'produced')))):
        raise HTTPException(409, '该批次已有下发或出餐记录，不能覆盖登记；请新建批次或登记替料')
    _check_version(batch, data.expected_version)
    if data.allergen_version is not None:
        if not data.allergen_version.strip():
            raise HTTPException(422, '过敏原版本不能为空')
        batch.allergen_version = data.allergen_version.strip()
    # 菜品份数登记
    portions = {}
    for item in data.dishes:
        if item.portions < 0:
            raise HTTPException(422, '菜品份数不能为负')
        dish = db.get(Dish, item.dish_id)
        if not dish:
            raise HTTPException(404, f'菜品 {item.dish_id} 不存在')
        if not dish.active:
            raise HTTPException(409, f'菜品「{dish.name}」已停用')
        if item.dish_id in portions:
            raise HTTPException(422, f'菜品「{dish.name}」重复登记')
        portions[item.dish_id] = int(item.portions)
    for old in list(db.scalars(select(BatchDish).where(BatchDish.batch_id == batch.id))):
        db.delete(old)
    db.flush()
    for dish_id, count in portions.items():
        db.add(BatchDish(batch_id=batch.id, dish_id=dish_id, portions=count))
    # 忌口组合登记
    combos = list(data.combos)
    names = set()
    used = 0
    cleaned = []
    for c in combos:
        name = c.name.strip()
        if len(name) < 2:
            raise HTTPException(422, '忌口组合名称至少两个字符')
        if name in names:
            raise HTTPException(422, f'忌口组合「{name}」重复')
        names.add(name)
        codes = [str(x) for x in c.exclusions]
        if len(codes) != len(set(codes)) or any(x not in ALLERGEN_LABELS for x in codes):
            raise HTTPException(422, f'忌口组合「{name}」含未知过敏原')
        if c.count < 1:
            raise HTTPException(422, f'忌口组合「{name}」份数必须为正整数')
        used += int(c.count)
        cleaned.append((name, codes, int(c.count)))
    if used > batch.total:
        raise HTTPException(422, f'忌口组合合计 {used} 份，超过当日已批准份数 {batch.total}')
    remainder = batch.total - used
    default_name = '普通餐（无特殊忌口）'
    if remainder > 0:
        if default_name in names:
            raise HTTPException(422, f'请把「{default_name}」的份数直接登记在组合中')
        cleaned.append((default_name, [], remainder))
    # 先解除份与旧组合的引用，再替换组合，避免外键阻止删除
    for meal in db.scalars(select(Meal).where(Meal.batch_id == batch.id)):
        meal.combo_id = None
    db.flush()
    for old in list(db.scalars(select(BatchCombo).where(BatchCombo.batch_id == batch.id))):
        db.delete(old)
    db.flush()
    combos_db = []
    for name, codes, count in cleaned:
        c = BatchCombo(batch_id=batch.id, name=name, exclusions=codes, count=count)
        db.add(c)
        combos_db.append(c)
    db.flush()
    # 重置每份餐的组合归属；登记容量保持不变
    meals = list(db.scalars(select(Meal).where(Meal.batch_id == batch.id).order_by(Meal.seq)))
    if len(meals) != batch.total:
        # 兼容历史批次缺少明细：补齐
        existing = {m.seq for m in meals}
        for seq in range(1, batch.total + 1):
            if seq not in existing:
                db.add(Meal(batch_id=batch.id, seq=seq, status='pending', label_snapshot={}, label_version='', basis={}, label_history=[], updated_at=now()))
        db.flush()
        meals = list(db.scalars(select(Meal).where(Meal.batch_id == batch.id).order_by(Meal.seq)))
    bounds = []
    cursor = 1
    for c in combos_db:
        bounds.append((cursor, cursor + c.count - 1, c.id))
        cursor += c.count
    for meal in meals:
        combo_id = None
        if 1 <= meal.seq <= batch.total:
            for start, end, cid in bounds:
                if start <= meal.seq <= end:
                    combo_id = cid
                    break
        meal.combo_id = combo_id
        meal.dish_id = None
        meal.preferred_dish_id = None
        meal.status = 'pending'
        meal.label_snapshot = {}
        meal.label_version = ''
        meal.basis = {}
        meal.label_history = []
        meal.dispatch_id = None
        meal.produced_at = None
        meal.updated_at = now()
    batch.status = 'pending'
    batch.shortage = {}
    batch.resume_status = None
    batch.version += 1
    batch.updated_at = now()
    _audit(db, user, 'meal.batch.configure', {'id': batch.id, 'version': batch.version, 'combos': len(cleaned), 'dishes': len(portions)})
    db.flush()
    return batch


def _check_version(batch, expected):
    if expected is None:
        return
    if int(expected) != batch.version:
        raise HTTPException(409, f'配餐版本已更新（当前 v{batch.version}），请刷新后按最新登记重试')


def _audit(db, user, action, detail):
    record(db, getattr(user, 'email', 'system'), action, detail)


# ---------------------------------------------------------------- matching

class _Dinic:
    """小规模组合-菜品容量最大流；份数只读，不递减登记容量。"""

    def __init__(self, n):
        self.n = n
        self.graph = [[] for _ in range(n)]
        self.edges = []

    def add(self, u, v, cap, meta=None):
        self.graph[u].append(len(self.edges))
        self.edges.append({'to': v, 'cap': cap, 'flow': 0, 'meta': meta})
        self.graph[v].append(len(self.edges))
        self.edges.append({'to': u, 'cap': 0, 'flow': 0, 'meta': None})

    def max_flow(self, s, t):
        total = 0
        while True:
            level = [-1] * self.n
            level[s] = 0
            queue = [s]
            for u in queue:
                for eid in self.graph[u]:
                    e = self.edges[eid]
                    if e['cap'] - e['flow'] > 0 and level[e['to']] < 0:
                        level[e['to']] = level[u] + 1
                        queue.append(e['to'])
            if level[t] < 0:
                break
            it = [0] * self.n

            def dfs(u, pushed):
                if u == t:
                    return pushed
                while it[u] < len(self.graph[u]):
                    eid = self.graph[u][it[u]]
                    e = self.edges[eid]
                    if e['cap'] - e['flow'] > 0 and level[e['to']] == level[u] + 1:
                        sent = dfs(e['to'], min(pushed, e['cap'] - e['flow']))
                        if sent:
                            e['flow'] += sent
                            self.edges[eid ^ 1]['flow'] -= sent
                            return sent
                    it[u] += 1
                return 0

            while True:
                sent = dfs(s, 10 ** 9)
                if not sent:
                    break
                total += sent
        return total


def _eligible_dishes(db, batch):
    dishes = list(db.scalars(select(Dish).order_by(Dish.id)))
    portions = {p.dish_id: p.portions for p in db.scalars(select(BatchDish).where(BatchDish.batch_id == batch.id))}
    eligible, paused = [], []
    for d in dishes:
        registered = portions.get(d.id)
        if registered is None:
            continue
        if not d.active:
            paused.append({'dish_id': d.id, 'code': d.code, 'name': d.name, 'portions': registered, 'reason': '菜品已停用，暂停分配'})
            continue
        if d.label_status != 'known':
            paused.append({'dish_id': d.id, 'code': d.code, 'name': d.name, 'portions': registered, 'reason': '过敏原标签不明，暂停分配'})
            continue
        if d.allergen_version != batch.allergen_version:
            paused.append({'dish_id': d.id, 'code': d.code, 'name': d.name, 'portions': registered, 'reason': f'标签版本 {d.allergen_version} 与批次 {batch.allergen_version} 不一致，暂停分配'})
            continue
        eligible.append(d)
    return eligible, paused, portions


def allocate(db, batch, expected_version, user, persist_state=True):
    """按当前登记重算整批。任何失败都会保留登记容量，可安全重试。"""
    if batch.status == 'allocating':
        # 上一次中断：直接按原批继续，未递减的登记容量保证不会重复占用
        batch.resume_status = batch.resume_status or 'pending'
    elif batch.status == 'dispatched' and not db.scalar(select(Meal.id).where(Meal.batch_id == batch.id, Meal.status == 'held')):
        raise HTTPException(409, '该批次已全部下发；如需调整请先登记替料')
    elif batch.status == 'dispatched' and not persist_state:
        # 替料挂起后的重配：已下发份不变，只重新配平挂起份
        pass
    _check_version(batch, expected_version)

    meals = list(db.scalars(select(Meal).where(Meal.batch_id == batch.id).order_by(Meal.seq)))
    combos = list(db.scalars(select(BatchCombo).where(BatchCombo.batch_id == batch.id).order_by(BatchCombo.id)))
    if not combos:
        raise HTTPException(409, '请先登记忌口组合和菜品份数')
    if sum(c.count for c in combos) != batch.total:
        raise HTTPException(409, '忌口组合合计与当日份数不一致，请重新登记')

    # 中断恢复只回到可继续配餐的状态；已下发份仍由 dispatched 状态与下发单保护
    if batch.status in ('ready', 'short'):
        snapshot = batch.status
    elif batch.dispatched_at:
        # 已下发批次因替料重配中断：恢复为待补，提醒仍有份需要配平后再补发
        snapshot = 'short'
    else:
        snapshot = 'pending'
    batch.status = 'allocating'
    batch.resume_status = snapshot
    batch.updated_at = now()
    if persist_state:
        # 先落盘“配餐中”，进程中断后可凭 resume_status 按原批恢复
        db.commit()

    try:
        result = _run_allocation(db, batch, meals, combos)
    except Exception:
        batch.status = batch.resume_status or 'pending'
        batch.resume_status = None
        batch.updated_at = now()
        db.commit()
        raise
    batch.resume_status = None
    batch.version += 1
    batch.updated_at = now()
    _audit(db, user, 'meal.batch.allocate', {
        'id': batch.id, 'version': batch.version, 'status': batch.status,
        'missing': result.get('missing', 0),
    })
    db.flush()
    return batch, result


def _run_allocation(db, batch, meals, combos):
    dish_map = {d.id: d for d in db.scalars(select(Dish))}
    combo_map = {c.id: c for c in combos}
    pool = [m for m in meals if m.status in ('pending', 'held', 'allocated')]
    locked = [m for m in meals if m.status in ('produced', 'dispatched')]

    # 已出餐与未受影响的已下发份保留原标签，并占用对应容量
    used = defaultdict(int)
    for m in locked:
        if m.dish_id:
            used[m.dish_id] += 1

    eligible, paused, portions = _eligible_dishes(db, batch)
    capacity = {}
    for d in eligible:
        capacity[d.id] = max(0, portions.get(d.id, 0) - used.get(d.id, 0))

    demands = defaultdict(int)
    for m in pool:
        demands[m.combo_id] += 1

    active_combos = [c for c in combos if demands.get(c.id, 0) > 0]
    # 若容量全部不可用仍要给出可读的缺口
    dish_index = {d.id: i for i, d in enumerate(eligible)}
    combo_index = {c.id: len(eligible) + i for i, c in enumerate(active_combos)}
    source, sink = 0, len(eligible) + len(active_combos) + 1
    flow = _Dinic(sink + 1)
    for d in eligible:
        flow.add(source, 1 + dish_index[d.id], capacity[d.id])
    for c in active_combos:
        flow.add(combo_index[c.id] + 1, sink, demands[c.id])
    edge_meta = {}
    for c in active_combos:
        excluded = set(c.exclusions or [])
        for d in eligible:
            if excluded & set(d.allergens or []):
                continue
            eid = len(flow.edges)
            flow.add(1 + dish_index[d.id], 1 + combo_index[c.id], demands[c.id], meta=(c.id, d.id))
            edge_meta[(c.id, d.id)] = eid
    matched = flow.max_flow(source, sink)
    pool_size = len(pool)

    if matched < pool_size:
        # 整批待补：不生成任何新标签
        for m in pool:
            if m.status == 'held':
                continue  # 保持挂起，保留旧标签历史
            m.dish_id = None
            m.preferred_dish_id = None
            m.basis = {}
            m.status = 'pending'
            m.updated_at = now()
        shortage = {
            'missing': pool_size - matched,
            'needed': pool_size,
            'allocatable': matched,
            'combos': [],
            'paused_dishes': paused,
            'capacity': [
                {'dish_id': d.id, 'code': d.code, 'name': d.name, 'registered': portions.get(d.id, 0), 'available': capacity.get(d.id, 0), 'locked': used.get(d.id, 0)}
                for d in eligible
            ],
        }
        for c in active_combos:
            want = demands[c.id]
            filled = sum(flow.edges[e]['flow'] for (cid, _), e in edge_meta.items() if cid == c.id)
            if filled < want:
                shortage['combos'].append({
                    'combo_id': c.id, 'name': c.name, 'exclusions': c.exclusions or [],
                    'exclusion_labels': label_list(c.exclusions),
                    'need': want, 'filled': filled, 'missing': want - filled,
                })
        shortage['hint'] = '整批待补，配齐前不会下发；可补充菜品份数或登记替料后重新配餐。'
        batch.status = 'short'
        batch.shortage = shortage
        return {'missing': shortage['missing'], 'shortage': shortage}

    # 完全匹配：按组合的流量分配具体份数；挂起份优先落到替料目标菜
    remaining = defaultdict(int)
    for (cid, did), eid in edge_meta.items():
        amount = flow.edges[eid]['flow']
        if amount:
            remaining[(cid, did)] = amount
    # 先处理带替料目标的挂起份，确保替入菜品容量优先落到被挂起的份
    pool_sorted = sorted(pool, key=lambda m: (m.preferred_dish_id is None, m.seq))
    history_updates = []
    for m in pool_sorted:
        prefix = (m.combo_id,)
        choices = []
        for (cid, did), amount in remaining.items():
            if cid == m.combo_id and amount > 0:
                choices.append(did)
        if not choices:
            # 理论上不会发生：流量已覆盖全部需求
            raise RuntimeError('配餐流量与份数不一致')
        preferred = m.preferred_dish_id
        chosen = preferred if preferred in choices else choices[0]
        remaining[(m.combo_id, chosen)] -= 1
        dish = dish_map[chosen]
        combo = combo_map[m.combo_id]
        snapshot = {
            'dish_id': dish.id,
            'dish_code': dish.code,
            'dish_name': dish.name,
            'allergens': sorted(dish.allergens or []),
            'allergen_labels': label_list(dish.allergens),
            'label_status': dish.label_status,
            'allergen_version': dish.allergen_version,
            'label_hash': label_hash(dish),
        }
        if m.label_snapshot and m.dish_id and m.dish_id != chosen:
            history_updates.append((m, {
                'replaced_at': now(),
                'reason': '重新配餐',
                'previous': m.label_snapshot,
            }))
        m.dish_id = dish.id
        m.preferred_dish_id = None
        m.status = 'allocated'
        m.dispatch_id = None
        m.label_snapshot = snapshot
        m.label_version = dish.allergen_version
        m.basis = {
            'combo': {'id': combo.id, 'name': combo.name, 'exclusions': combo.exclusions or [], 'exclusion_labels': label_list(combo.exclusions)},
            'dish': snapshot,
            'rule': '忌口与菜品过敏原无交集；菜品标签明确且版本与批次一致；按登记份数分配',
            'allergen_version': batch.allergen_version,
            'batch_version': batch.version + 1,
            'registered_portions': portions.get(dish.id, 0),
            'preferred_match': preferred == chosen,
        }
        m.updated_at = now()
    for m, entry in history_updates:
        m.label_history = list(m.label_history or []) + [entry]

    batch.status = 'ready'
    batch.shortage = {}
    return {'missing': 0}


def recover_batches(db, actor='system'):
    """启动或重试时恢复中断在 allocating 的批次；容量未递减，重配不会重复占用。"""
    recovered = []
    for b in db.scalars(select(MealBatch).where(MealBatch.status == 'allocating')):
        prev = b.resume_status or 'pending'
        b.status = prev
        b.resume_status = None
        b.updated_at = now()
        record(db, actor, 'meal.batch.recovered', {'id': b.id, 'restored_to': prev})
        recovered.append(b.id)
    if recovered:
        db.commit()
    return recovered


# ---------------------------------------------------------------- substitution

def _lock_batch(db, batch):
    """行锁并强制重读，保证并发替料/下发时看到已提交的最新版本。"""
    locked = db.scalar(
        select(MealBatch)
        .where(MealBatch.id == batch.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return locked or batch


def substitute(db, batch, data, user):
    batch = _lock_batch(db, batch)
    if batch.status == 'allocating':
        raise HTTPException(409, '配餐正在进行，请稍后按最新版本登记替料')
    _check_version(batch, data.expected_version)
    if data.from_dish_id == data.to_dish_id:
        raise HTTPException(422, '替入菜品不能与替出菜品相同')
    frm = db.get(Dish, data.from_dish_id)
    to = db.get(Dish, data.to_dish_id)
    if not frm or not to:
        raise HTTPException(404, '替料菜品不存在')
    if not to.active:
        raise HTTPException(409, f'替入菜品「{to.name}」已停用')
    count = int(data.count)
    if count < 1:
        raise HTTPException(422, '替料份数必须为正整数')
    if len((data.reason or '').strip()) < 2:
        raise HTTPException(422, '请填写替料原因，便于复核')
    if to.label_status != 'known':
        raise HTTPException(409, f'替入菜品「{to.name}」标签不明，不能用于配餐')
    if to.allergen_version != batch.allergen_version:
        raise HTTPException(409, f'替入菜品标签版本 {to.allergen_version} 与批次 {batch.allergen_version} 不一致')

    rows = {p.dish_id: p for p in db.scalars(select(BatchDish).where(BatchDish.batch_id == batch.id))}
    if data.from_dish_id not in rows:
        raise HTTPException(409, f'替出菜品「{frm.name}」尚未在本批登记份数')
    if rows[data.from_dish_id].portions < count:
        raise HTTPException(422, f'替出菜品「{frm.name}」登记份数不足 {count} 份')

    affected = list(db.scalars(select(Meal).where(
        Meal.batch_id == batch.id,
        Meal.dish_id == data.from_dish_id,
        Meal.status.in_(('allocated', 'dispatched')),
    ).order_by(Meal.seq)))
    if batch.status == 'dispatched' and not affected:
        raise HTTPException(409, '没有可替换的未出餐份；已出餐与已下发的份保留原标签')
    if count > len(affected):
        raise HTTPException(422, f'「{frm.name}」当前只有 {len(affected)} 份未出餐可替换，请减少替料份数')
    # 只挂起与替料份数相等的未出餐份；其余已下发份保留原标签。
    # 优先挂起替入菜品能满足其忌口的份，避免无谓地改动其他标签。
    combos = {c.id: c for c in db.scalars(select(BatchCombo).where(BatchCombo.batch_id == batch.id))}
    dish_allergens = set(to.allergens or [])

    def _compatible(m):
        combo = combos.get(m.combo_id)
        return not (dish_allergens & set(combo.exclusions or [])) if combo else True

    affected = sorted(affected, key=lambda m: (not _compatible(m), m.seq))[:count]
    if any(not _compatible(m) for m in affected):
        raise HTTPException(422, f'替入菜品「{to.name}」的过敏原与部分待替换份的忌口组合冲突，请减少份数或改用其他菜品')

    if data.to_dish_id not in rows:
        rows[data.to_dish_id] = BatchDish(batch_id=batch.id, dish_id=to.id, portions=0)
        db.add(rows[data.to_dish_id])
        db.flush()
    rows[data.from_dish_id].portions -= count
    rows[data.to_dish_id].portions += count

    affected_seqs = []
    for m in affected:
        affected_seqs.append(m.seq)
        m.status = 'held'
        m.preferred_dish_id = data.to_dish_id
        if m.label_snapshot:
            m.label_history = list(m.label_history or []) + [{
                'replaced_at': now(),
                'reason': '替料挂起：' + data.reason.strip(),
                'previous': m.label_snapshot,
                'keeps_dispatch': m.dispatch_id,
            }]
        m.updated_at = now()

    sub = Substitution(
        batch_id=batch.id,
        from_dish_id=data.from_dish_id,
        to_dish_id=data.to_dish_id,
        count=count,
        applied_count=len(affected_seqs),
        reason=data.reason.strip(),
        actor=user.email,
        affected=affected_seqs,
        version_after=batch.version,
        created_at=now(),
    )
    db.add(sub)
    batch.version += 1
    batch.updated_at = now()
    db.flush()

    if batch.status in ('ready', 'short', 'dispatched'):
        # 替料与重配保持同一事务：任一步失败则整体回滚
        batch, result = allocate(db, batch, None, user, persist_state=False)
    else:
        result = {'missing': 0}
        batch.status = 'pending'
    sub.version_after = batch.version
    _audit(db, user, 'meal.substitution', {
        'id': batch.id, 'from': frm.code, 'to': to.code, 'count': count,
        'affected': affected_seqs, 'version': batch.version,
    })
    db.flush()
    return batch, sub, result


# ---------------------------------------------------------------- dispatch

def dispatch(db, batch, expected_version, user):
    # MySQL 下行锁把替料与下发串行化；版本再校验一次，杜绝过期配餐
    batch = _lock_batch(db, batch)
    if expected_version is None or int(expected_version) != batch.version:
        raise HTTPException(409, f'配餐已过期（当前 v{batch.version}），可能有替料刚刚登记，请重新核对后再下发')
    if batch.status != 'ready':
        raise HTTPException(409, '只有配齐待下发的批次可以下发；待补批次请先补齐份数')
    dishes = {d.id: d for d in db.scalars(select(Dish))}
    combos = {c.id: c for c in db.scalars(select(BatchCombo).where(BatchCombo.batch_id == batch.id))}
    meals = list(db.scalars(select(Meal).where(Meal.batch_id == batch.id).order_by(Meal.seq)))
    targets = [m for m in meals if m.status == 'allocated']
    if not targets:
        raise HTTPException(409, '没有待下发的份数')
    if any(m.status in ('pending', 'held') for m in meals):
        raise HTTPException(409, '仍有未配齐的份数，整批不可下发')

    # 标签依据必须与当前菜品标签一致，并锁定同一过敏原版本。
    # 已下发但未出餐的份（此前替料产生的新单）也按同一规则核对。
    stale = []
    for m in meals:
        if m.status not in ('allocated', 'dispatched'):
            continue
        d = dishes.get(m.dish_id)
        if not d or d.label_status != 'known' or d.allergen_version != batch.allergen_version or label_hash(d) != (m.label_snapshot or {}).get('label_hash'):
            stale.append(m.seq)
            m.status = 'held'
            m.preferred_dish_id = None
            m.dispatch_id = None
            m.updated_at = now()
    if stale:
        batch.status = 'short'
        batch.shortage = {'missing': len(stale), 'needed': len(meals), 'allocatable': len(meals) - len(stale), 'stale_meals': stale, 'hint': '过敏原标签已更新，受影响份已挂起；请重新配餐后再下发。'}
        batch.version += 1
        batch.updated_at = now()
        _audit(db, user, 'meal.dispatch.rejected', {'id': batch.id, 'stale': len(stale)})
        # 先落盘挂起状态再报错，避免整单回滚后旧标签仍可下发
        db.commit()
        raise HTTPException(409, '菜品过敏原标签在配餐后发生变化，已阻止下发；受影响份已挂起，请重新配餐')

    labels = []
    for m in targets:
        d = dishes[m.dish_id]
        c = combos.get(m.combo_id)
        labels.append({
            'seq': m.seq,
            'combo': c.name if c else '',
            'exclusions': label_list(c.exclusions) if c else [],
            'dish_code': d.code,
            'dish_name': d.name,
            'allergens': label_list(d.allergens),
            'allergen_version': d.allergen_version,
            'label_hash': m.label_snapshot.get('label_hash'),
            'basis': m.basis,
        })
    payload = {
        'batch': {'id': batch.id, 'name': batch.name, 'date': batch.date, 'total': batch.total, 'allergen_version': batch.allergen_version, 'version': batch.version},
        'dispatched_at': now(),
        'count': len(labels),
        'labels': labels,
    }
    data_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
    checksum = hashlib.sha256(data_bytes).hexdigest()
    row = MealDispatch(
        batch_id=batch.id, created_by=user.id, total=len(labels),
        allergen_version=batch.allergen_version, checksum=checksum,
        filename='', created_at=now(),
    )
    db.add(row)
    db.flush()
    row.filename = f'meal-batch-{batch.id}-dispatch-{row.id}.json'
    from src.config.settings import ARTIFACT_DIR
    import os
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    with open(os.path.join(ARTIFACT_DIR, row.filename), 'wb') as fh:
        fh.write(data_bytes)
    with open(os.path.join(ARTIFACT_DIR, row.filename + '.csv'), 'w', newline='', encoding='utf-8-sig') as fh:
        writer = csv.writer(fh)
        writer.writerow(['份号', '忌口组合', '忌口', '菜品编码', '菜品', '过敏原', '过敏原版本', '标签指纹'])
        for item in labels:
            writer.writerow([item['seq'], item['combo'], '、'.join(item['exclusions']) or '无', item['dish_code'], item['dish_name'], '、'.join(item['allergens']) or '无', item['allergen_version'], item['label_hash']])

    for m in targets:
        m.status = 'dispatched'
        m.dispatch_id = row.id
        m.label_history = list(m.label_history or []) + [{
            'dispatched_at': row.created_at,
            'dispatch_id': row.id,
            'label': m.label_snapshot,
        }]
        m.updated_at = now()
    batch.status = 'dispatched'
    batch.dispatched_at = row.created_at
    batch.shortage = {}
    batch.version += 1
    batch.updated_at = now()
    _audit(db, user, 'meal.dispatch', {'id': batch.id, 'dispatch': row.id, 'count': len(labels), 'version': batch.version})
    db.flush()
    return batch, row
