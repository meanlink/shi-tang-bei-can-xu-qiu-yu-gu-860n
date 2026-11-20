from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
import os

from src.config.database import get_db
from src.config.settings import ARTIFACT_DIR
from src.constants.allergens import catalog
from src.middlewares.auth import current_user, can_write, admin_only
from src.models import Dish, Meal, MealBatch, MealDispatch
from src.schemas.meal import (
    AllocateInput,
    BatchInput,
    ConfigureInput,
    DishInput,
    DishPatch,
    DispatchInput,
    SubstitutionInput,
)
from src.services import meals
from src.services.audit import record

router = APIRouter(prefix='/meals')


@router.get('/catalog')
def meta(u=Depends(current_user)):
    return catalog()


@router.get('/dishes')
def dishes(db=Depends(get_db), u=Depends(current_user)):
    return [meals.dish_dict(d) for d in db.scalars(select(Dish).order_by(Dish.code))]


@router.post('/dishes', status_code=201)
def create_dish(data: DishInput, db=Depends(get_db), u=Depends(can_write)):
    d = meals.create_dish(db, data)
    record(db, u.email, 'meal.dish.create', {'id': d.id, 'code': d.code, 'version': d.allergen_version})
    db.commit()
    return meals.dish_dict(d)


@router.patch('/dishes/{id}')
def patch_dish(id: int, data: DishPatch, db=Depends(get_db), u=Depends(can_write)):
    d = db.get(Dish, id)
    if not d:
        raise HTTPException(404, '菜品不存在')
    meals.update_dish(db, d, data)
    record(db, u.email, 'meal.dish.update', {'id': d.id, 'version': d.allergen_version, 'status': d.label_status})
    db.commit()
    return meals.dish_dict(d)


@router.get('/batches')
def batches(db=Depends(get_db), u=Depends(current_user)):
    return [meals.batch_dict(b) for b in db.scalars(select(MealBatch).order_by(MealBatch.id.desc()).limit(100))]


@router.post('/batches', status_code=201)
def create_batch(data: BatchInput, db=Depends(get_db), u=Depends(can_write)):
    b = meals.create_batch(db, data, u)
    record(db, u.email, 'meal.batch.create', {'id': b.id, 'date': b.date, 'total': b.total})
    db.commit()
    return meals.batch_dict(b, db)


@router.get('/batches/{id}')
def batch_detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    b = db.get(MealBatch, id)
    if not b:
        raise HTTPException(404, '配餐批次不存在')
    return meals.batch_dict(b, db, detail=True)


@router.put('/batches/{id}/configuration')
def configure(id: int, data: ConfigureInput, db=Depends(get_db), u=Depends(can_write)):
    b = db.get(MealBatch, id)
    if not b:
        raise HTTPException(404, '配餐批次不存在')
    meals.configure_batch(db, b, data, u)
    db.commit()
    return meals.batch_dict(db.get(MealBatch, id), db, detail=True)


@router.post('/batches/{id}/allocate')
def allocate(id: int, data: AllocateInput, db=Depends(get_db), u=Depends(can_write)):
    b = db.get(MealBatch, id)
    if not b:
        raise HTTPException(404, '配餐批次不存在')
    b, result = meals.allocate(db, b, data.expected_version, u)
    db.commit()
    return {'batch': meals.batch_dict(db.get(MealBatch, id), db, detail=True), 'missing': result.get('missing', 0)}


@router.post('/batches/{id}/substitutions', status_code=201)
def substitute(id: int, data: SubstitutionInput, db=Depends(get_db), u=Depends(can_write)):
    b = db.get(MealBatch, id)
    if not b:
        raise HTTPException(404, '配餐批次不存在')
    b, sub, result = meals.substitute(db, b, data, u)
    dishes_map = {d.id: d for d in db.scalars(select(Dish))}
    db.commit()
    return {'batch': meals.batch_dict(db.get(MealBatch, id), db, detail=True), 'substitution': meals.substitution_dict(sub, dishes_map)}


@router.post('/batches/{id}/dispatch')
def dispatch(id: int, data: DispatchInput, db=Depends(get_db), u=Depends(admin_only)):
    b = db.get(MealBatch, id)
    if not b:
        raise HTTPException(404, '配餐批次不存在')
    b, row = meals.dispatch(db, b, data.expected_version, u)
    db.commit()
    return {'batch': meals.batch_dict(db.get(MealBatch, id), db, detail=True), 'dispatch': meals.dispatch_dict(row)}


@router.post('/batches/{id}/recover')
def recover(id: int, db=Depends(get_db), u=Depends(can_write)):
    b = db.get(MealBatch, id)
    if not b:
        raise HTTPException(404, '配餐批次不存在')
    if b.status != 'allocating':
        raise HTTPException(409, '该批次未处于中断状态，无需恢复')
    prev = b.resume_status or 'pending'
    b.status = prev
    b.resume_status = None
    b.updated_at = meals.now()
    record(db, u.email, 'meal.batch.recovered', {'id': b.id, 'restored_to': prev})
    db.commit()
    return meals.batch_dict(db.get(MealBatch, id), db, detail=True)


@router.post('/batches/{id}/meals/{meal_id}/produce')
def produce(id: int, meal_id: int, db=Depends(get_db), u=Depends(can_write)):
    m = db.get(Meal, meal_id)
    if not m or m.batch_id != id:
        raise HTTPException(404, '该份餐不存在')
    if m.status != 'dispatched':
        raise HTTPException(409, '只有已下发的份可以标记出餐')
    m.status = 'produced'
    m.produced_at = meals.now()
    m.updated_at = meals.now()
    record(db, u.email, 'meal.produce', {'batch': id, 'seq': m.seq})
    db.commit()
    return {'id': m.id, 'seq': m.seq, 'status': m.status}


@router.get('/dispatches/{id}/download')
def download(id: int, format: str = 'json', db=Depends(get_db), u=Depends(current_user)):
    d = db.get(MealDispatch, id)
    if not d:
        raise HTTPException(404, '下发单不存在')
    if format not in ('csv', 'json'):
        raise HTTPException(422, '支持 CSV 或 JSON')
    filename = d.filename if format == 'json' else d.filename + '.csv'
    path = os.path.join(ARTIFACT_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404, '下发文件不存在')
    return FileResponse(path, filename=filename, media_type='application/json' if format == 'json' else 'text/csv; charset=utf-8')
