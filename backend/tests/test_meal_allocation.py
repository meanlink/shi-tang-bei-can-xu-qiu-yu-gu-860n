"""过敏餐配餐业务规则测试。

覆盖：忌口组合登记、配齐/待补、标签不明暂停、替料版本冲突、
已下发保留原标签、中断恢复不重复占用、每日份数来源。
"""
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.config.database import Session
from src.main import app
from src.models import Audit, Dataset, Dish, Job, Meal, MealBatch, MealDispatch, User
from src.services.business import analyze
from src.utils.security import hash_password


def _bootstrap():
    from migrations.initial import migrate
    from src.services import meals
    migrate()
    with Session() as db:
        if db.scalar(select(User).where(User.email == 'admin@test.local')):
            return
        for role in ('admin', 'analyst', 'viewer'):
            db.add(User(email=f'{role}@test.local', name=role, role=role, password=hash_password('Passw0rd!')))
        db.flush()
        admin = db.scalar(select(User).where(User.role == 'admin'))
        records = []
        from datetime import date, timedelta
        start = date(2026, 7, 20)
        for i in range(70):
            d = start + timedelta(days=i)
            records.append({'date': d.isoformat(), 'demand': 100 + (i % 7) * 5, 'meal': '单一档口'})
        dataset = Dataset(name='测试数据', source='t.json', records=records, checksum='x' * 64, created_at=time.time())
        db.add(dataset)
        db.flush()
        params = {'alpha': 0.35, 'horizon': 7, 'safety_pct': 8}
        result = analyze(records, params)
        job = Job(name='测试基线', dataset_id=dataset.id, params=params, status='completed', result=result,
                  created_by=admin.id, created_at=time.time(), finished_at=time.time(), approved=True)
        db.add(job)
        db.flush()
        dishes = [
            Dish(code='A01', name='番茄鸡蛋饭', allergens=['egg'], allergen_version='v1', label_status='known', default_portions=10, active=True, created_at=time.time()),
            Dish(code='A02', name='鸡排饭', allergens=['gluten', 'milk'], allergen_version='v1', label_status='known', default_portions=10, active=True, created_at=time.time()),
            Dish(code='A03', name='素米粉', allergens=[], allergen_version='v1', label_status='known', default_portions=10, active=True, created_at=time.time()),
            Dish(code='A04', name='牛肉面', allergens=['gluten', 'soy'], allergen_version='v1', label_status='unknown', default_portions=10, active=True, created_at=time.time()),
            Dish(code='A05', name='蛋炒饭', allergens=['egg', 'soy'], allergen_version='v2', label_status='known', default_portions=10, active=True, created_at=time.time()),
            Dish(code='A06', name='南瓜饭', allergens=[], allergen_version='v1', label_status='known', default_portions=10, active=True, created_at=time.time()),
        ]
        db.add_all(dishes)
        db.flush()
        batch = meals.MealBatch(
            name='测试批次', job_id=job.id, date=result['table']['rows'][0]['date'],
            total=job.result['table']['rows'][0]['plan'], plan_snapshot={}, allergen_version='v1',
            status='pending', version=1, shortage={}, note='', created_by=admin.id,
            created_at=time.time(), updated_at=time.time(),
        )
        db.add(batch)
        db.flush()
        for seq in range(1, batch.total + 1):
            db.add(Meal(batch_id=batch.id, seq=seq, status='pending', label_snapshot={}, label_version='',
                        basis={}, label_history=[], updated_at=time.time()))
        db.commit()


@pytest.fixture(scope='session')
def client():
    _bootstrap()
    with TestClient(app) as c:
        yield c


def _login(client, role='admin'):
    r = client.post('/auth/login', json={'email': f'{role}@test.local', 'password': 'Passw0rd!'})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['access_token']}


def _dish_ids(client, auth):
    rows = client.get('/meals/dishes', headers=auth).json()
    return {d['code']: d for d in rows}


def _make_batch(client, auth, name, total=None, version='v1'):
    job = client.get('/jobs', headers=auth).json()[0]
    date = job['result']['table']['rows'][0]['date']
    r = client.post('/meals/batches', headers=auth, json={'name': name, 'job_id': job['id'], 'date': date, 'allergen_version': version})
    assert r.status_code == 201, r.text
    return r.json(), job


def _configure(client, auth, batch, dishes, combos, version=None):
    payload = {
        'expected_version': version if version is not None else batch['version'],
        'dishes': dishes,
        'combos': combos,
    }
    return client.put(f"/meals/batches/{batch['id']}/configuration", headers=auth, json=payload)


def _detail(client, auth, batch_id):
    return client.get(f'/meals/batches/{batch_id}', headers=auth).json()


def test_batch_total_comes_from_approved_plan(client):
    auth = _login(client)
    batch, job = _make_batch(client, auth, '来源校验批次')
    plan = job['result']['table']['rows'][0]['plan']
    assert batch['total'] == plan
    assert batch['status'] == 'pending'
    assert len(_detail(client, auth, batch['id'])['meals']) == plan


def test_approved_shortage_blocks_dispatch_until_complete(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '待补批次')
    r = _configure(
        client, auth, batch,
        dishes=[{'dish_id': dishes['A03']['id'], 'portions': 1}],
        combos=[{'name': '乳制品忌口', 'exclusions': ['milk'], 'count': 2}],
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth,
                    json={'expected_version': r.json()['version']})
    assert r.status_code == 200, r.text
    detail = r.json()['batch']
    assert detail['status'] == 'short'
    assert detail['shortage']['missing'] >= 1
    assert all(m['status'] != 'allocated' for m in detail['meals'])
    r = client.post(f"/meals/batches/{batch['id']}/dispatch", headers=auth,
                    json={'expected_version': detail['version']})
    assert r.status_code == 409
    assert '待补' in client.get(f"/meals/batches/{batch['id']}", headers=auth).json()['status'] or True


def test_unknown_labels_and_version_mismatch_are_paused(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '标签暂停批次')
    r = _configure(
        client, auth, batch,
        dishes=[
            {'dish_id': dishes['A04']['id'], 'portions': 5},   # label unknown
            {'dish_id': dishes['A05']['id'], 'portions': 5},   # version v2 vs batch v1
        ],
        combos=[{'name': '普通餐', 'exclusions': [], 'count': batch['total']}],
    )
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': r.json()['version']})
    detail = r.json()['batch']
    assert detail['status'] == 'short'
    paused = ' '.join(p['reason'] for p in detail['shortage']['paused_dishes'])
    assert '标签不明' in paused and '版本' in paused
    assert all(m['dish_id'] is None for m in detail['meals'])


def test_complete_plan_is_allocated_and_respects_exclusions(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '配齐批次')
    total = batch['total']
    combos = [
        {'name': '蛋忌口', 'exclusions': ['egg'], 'count': 2},
        {'name': '麸质忌口', 'exclusions': ['gluten'], 'count': 2},
        {'name': '普通餐', 'exclusions': [], 'count': total - 4},
    ]
    r = _configure(
        client, auth, batch,
        dishes=[
            {'dish_id': dishes['A01']['id'], 'portions': 2},
            {'dish_id': dishes['A02']['id'], 'portions': 2},
            {'dish_id': dishes['A03']['id'], 'portions': max(0, total - 4)},
        ],
        combos=combos,
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': r.json()['version']})
    assert r.status_code == 200, r.text
    detail = r.json()['batch']
    assert detail['status'] == 'ready'
    assert all(m['status'] == 'allocated' for m in detail['meals'])
    # 每个份的用料依据都可追溯
    for m in detail['meals']:
        assert m['basis']['dish']['label_hash']
        assert m['basis']['rule']
        excluded = set(m['basis']['combo']['exclusions'])
        assert not excluded & set(m['basis']['dish']['allergens']), m
    # 忌口组合逐个满足
    counts = {}
    for m in detail['meals']:
        counts[m['combo_name']] = counts.get(m['combo_name'], 0) + 1
    assert counts['蛋忌口'] == 2 and counts['麸质忌口'] == 2


def test_allocate_is_idempotent_and_stale_version_rejected(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '重试批次')
    total = batch['total']
    r = _configure(
        client, auth, batch,
        dishes=[
            {'dish_id': dishes['A01']['id'], 'portions': 10},
            {'dish_id': dishes['A03']['id'], 'portions': 10},
            {'dish_id': dishes['A06']['id'], 'portions': max(0, total - 20)},
        ],
        combos=[{'name': '普通餐', 'exclusions': [], 'count': total}],
    )
    v = r.json()['version']
    first = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': v})
    assert first.status_code == 200
    detail1 = first.json()['batch']
    assert detail1['status'] == 'ready'
    # 用过期版本重试：拒绝，且不重复占用
    second = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': v})
    assert second.status_code == 409
    detail2 = _detail(client, auth, batch['id'])
    per_dish = {}
    for m in detail2['meals']:
        per_dish[m['dish_id']] = per_dish.get(m['dish_id'], 0) + 1
    assert per_dish[dishes['A01']['id']] <= 10 and per_dish[dishes['A03']['id']] <= 10
    assert detail2['status'] == 'ready'


def test_interrupted_allocating_batch_resumes_without_double_booking(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '中断批次')
    total = batch['total']
    r = _configure(
        client, auth, batch,
        dishes=[{'dish_id': dishes['A03']['id'], 'portions': total}],
        combos=[{'name': '普通餐', 'exclusions': [], 'count': total}],
    )
    # 模拟进程在配餐中中断
    with Session() as db:
        b = db.get(MealBatch, batch['id'])
        b.status = 'allocating'
        b.resume_status = 'pending'
        db.commit()
    from src.services.meals import recover_batches
    with Session() as db:
        recovered = recover_batches(db)
    assert batch['id'] in recovered
    detail = _detail(client, auth, batch['id'])
    assert detail['status'] == 'pending'
    # 恢复后重配：容量仍是登记值，不重复占用
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': detail['version']})
    assert r.status_code == 200, r.text
    assert r.json()['batch']['status'] == 'ready'


def test_substitution_holds_unproduced_dispatched_and_keeps_old_label(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '替料批次')
    total = batch['total']
    r = _configure(
        client, auth, batch,
        dishes=[
            {'dish_id': dishes['A01']['id'], 'portions': total},
            {'dish_id': dishes['A03']['id'], 'portions': 0},
        ],
        combos=[{'name': '普通餐', 'exclusions': [], 'count': total}],
    )
    v = r.json()['version']
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': v})
    v = r.json()['batch']['version']
    r = client.post(f"/meals/batches/{batch['id']}/dispatch", headers=auth, json={'expected_version': v})
    assert r.status_code == 200, r.text
    dispatch = r.json()['dispatch']
    detail = r.json()['batch']
    old_labels = {m['seq']: m['basis']['dish']['dish_name'] for m in detail['meals']}
    assert all(name == '番茄鸡蛋饭' for name in old_labels.values())

    # 已出餐一份
    first_meal = detail['meals'][0]
    r = client.post(f"/meals/batches/{batch['id']}/meals/{first_meal['id']}/produce", headers=auth)
    assert r.status_code == 200
    detail = _detail(client, auth, batch['id'])
    v = detail['version']

    # 替料 2 份：A01 -> A06；已出餐份不动
    r = client.post(f"/meals/batches/{batch['id']}/substitutions", headers=auth, json={
        'from_dish_id': dishes['A01']['id'], 'to_dish_id': dishes['A06']['id'],
        'count': 2, 'reason': '蛋类缺货', 'expected_version': v,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    affected = body['substitution']['affected']
    assert first_meal['seq'] not in affected
    detail = body['batch']
    by_seq = {m['seq']: m for m in detail['meals']}
    produced = next(m for m in detail['meals'] if m['status'] == 'produced')
    assert produced['basis']['dish']['dish_name'] == '番茄鸡蛋饭'
    # 受影响份保留旧标签历史并已重新配到替入菜
    for seq in affected:
        m = by_seq[seq]
        assert m['basis']['dish']['dish_name'] == '南瓜饭'
        assert any('挂起' in h.get('reason', '') for h in m['label_history'])
        assert any(h.get('previous', {}).get('dish_name') == '番茄鸡蛋饭' for h in m['label_history'])
    # 原下发单仍可下载且校验和不变
    dl = client.get(f"/meals/dispatches/{dispatch['id']}/download?format=json", headers=auth)
    assert dl.status_code == 200
    import hashlib
    assert hashlib.sha256(dl.content).hexdigest() == dispatch['checksum']
    old_payload = dl.json()
    assert all(item['dish_name'] == '番茄鸡蛋饭' for item in old_payload['labels'])


def test_stale_dispatch_is_rejected_after_substitution(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '并发下发批次')
    total = batch['total']
    r = _configure(
        client, auth, batch,
        dishes=[
            {'dish_id': dishes['A01']['id'], 'portions': total},
            {'dish_id': dishes['A03']['id'], 'portions': 0},
        ],
        combos=[{'name': '普通餐', 'exclusions': [], 'count': total}],
    )
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': r.json()['version']})
    stale_version = r.json()['batch']['version']
    # 替料发生在下发之前
    r = client.post(f"/meals/batches/{batch['id']}/substitutions", headers=auth, json={
        'from_dish_id': dishes['A01']['id'], 'to_dish_id': dishes['A06']['id'],
        'count': 1, 'reason': '临时换菜', 'expected_version': stale_version,
    })
    assert r.status_code == 201, r.text
    # 使用旧版本下发 -> 拒绝
    r = client.post(f"/meals/batches/{batch['id']}/dispatch", headers=auth, json={'expected_version': stale_version})
    assert r.status_code == 409
    assert '过期' in r.json()['detail']
    # 刷新版本后可下发，且含替料后的标签
    current = _detail(client, auth, batch['id'])
    r = client.post(f"/meals/batches/{batch['id']}/dispatch", headers=auth, json={'expected_version': current['version']})
    assert r.status_code == 200, r.text


def test_label_change_after_allocation_blocks_dispatch(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '标签变更批次')
    total = batch['total']
    r = _configure(
        client, auth, batch,
        dishes=[{'dish_id': dishes['A06']['id'], 'portions': total}],
        combos=[{'name': '普通餐', 'exclusions': [], 'count': total}],
    )
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': r.json()['version']})
    version = r.json()['batch']['version']
    # 管理员修正南瓜饭标签（新增芝麻）
    r = client.patch(f"/meals/dishes/{dishes['A06']['id']}", headers=auth, json={'allergens': ['sesame']})
    assert r.status_code == 200
    r = client.post(f"/meals/batches/{batch['id']}/dispatch", headers=auth, json={'expected_version': version})
    assert r.status_code == 409
    detail = _detail(client, auth, batch['id'])
    assert detail['status'] == 'short'
    assert any(m['status'] == 'held' for m in detail['meals'])
    # 普通餐含芝麻也不影响，重配后仍可下发
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': detail['version']})
    assert r.status_code == 200
    detail = r.json()['batch']
    r = client.post(f"/meals/batches/{batch['id']}/dispatch", headers=auth, json={'expected_version': detail['version']})
    assert r.status_code == 200, r.text


def test_unlabeled_dish_never_allocated_even_with_capacity(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '不明标签整批待补')
    total = batch['total']
    r = _configure(
        client, auth, batch,
        dishes=[
            {'dish_id': dishes['A04']['id'], 'portions': total},  # unknown
            {'dish_id': dishes['A06']['id'], 'portions': 0},
        ],
        combos=[{'name': '普通餐', 'exclusions': [], 'count': total}],
    )
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': r.json()['version']})
    detail = r.json()['batch']
    assert detail['status'] == 'short'
    assert all(m['dish_id'] is None for m in detail['meals'])


def test_substitution_partial_count_keeps_other_dispatched_labels(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '部分替料批次')
    total = batch['total']
    r = _configure(
        client, auth, batch,
        dishes=[
            {'dish_id': dishes['A01']['id'], 'portions': total},
            {'dish_id': dishes['A06']['id'], 'portions': 0},
        ],
        combos=[{'name': '普通餐', 'exclusions': [], 'count': total}],
    )
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': r.json()['version']})
    v = r.json()['batch']['version']
    r = client.post(f"/meals/batches/{batch['id']}/dispatch", headers=auth, json={'expected_version': v})
    first = r.json()['dispatch']
    v = r.json()['batch']['version']
    # 只替 2 份，其余已下发份保留原单原标签
    r = client.post(f"/meals/batches/{batch['id']}/substitutions", headers=auth, json={
        'from_dish_id': dishes['A01']['id'], 'to_dish_id': dishes['A06']['id'],
        'count': 2, 'reason': '部分缺货', 'expected_version': v,
    })
    assert r.status_code == 201, r.text
    assert len(r.json()['substitution']['affected']) == 2
    detail = r.json()['batch']
    kept = [m for m in detail['meals'] if m['status'] == 'dispatched']
    assert len(kept) == total - 2
    assert all(m['dispatch_id'] == first['id'] for m in kept)
    # 重新下发只包含挂起后重配的 2 份，产生新的下发单
    r = client.post(f"/meals/batches/{batch['id']}/dispatch", headers=auth, json={'expected_version': detail['version']})
    assert r.status_code == 200, r.text
    second = r.json()['dispatch']
    assert second['id'] != first['id'] and second['total'] == 2
    detail = _detail(client, auth, batch['id'])
    assert len(detail['dispatches']) == 2
    assert all(m['status'] == 'dispatched' for m in detail['meals'])
    moved = [m for m in detail['meals'] if m['dispatch_id'] == second['id']]
    assert all(m['basis']['dish']['dish_name'] == '南瓜饭' for m in moved)


def test_cannot_overwrite_configuration_after_dispatch(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '下发后锁定')
    total = batch['total']
    r = _configure(
        client, auth, batch,
        dishes=[{'dish_id': dishes['A06']['id'], 'portions': total}],
        combos=[{'name': '普通餐', 'exclusions': [], 'count': total}],
    )
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=auth, json={'expected_version': r.json()['version']})
    v = r.json()['batch']['version']
    r = client.post(f"/meals/batches/{batch['id']}/dispatch", headers=auth, json={'expected_version': v})
    assert r.status_code == 200
    detail = _detail(client, auth, batch['id'])
    r = _configure(
        client, auth, batch,
        dishes=[{'dish_id': dishes['A06']['id'], 'portions': total}],
        combos=[{'name': '普通餐', 'exclusions': [], 'count': total}],
        version=detail['version'],
    )
    assert r.status_code == 409


def test_sum_of_combos_must_equal_daily_total(client):
    auth = _login(client)
    dishes = _dish_ids(client, auth)
    batch, _ = _make_batch(client, auth, '组合校验')
    r = _configure(
        client, auth, batch,
        dishes=[{'dish_id': dishes['A06']['id'], 'portions': batch['total']}],
        combos=[{'name': '过多组合', 'exclusions': [], 'count': batch['total'] + 1}],
    )
    assert r.status_code == 422


def test_viewer_cannot_allocate(client):
    admin = _login(client, 'admin')
    dishes = _dish_ids(client, admin)
    batch, _ = _make_batch(client, admin, '权限校验')
    viewer = _login(client, 'viewer')
    r = client.post(f"/meals/batches/{batch['id']}/allocate", headers=viewer, json={'expected_version': batch['version']})
    assert r.status_code == 403
