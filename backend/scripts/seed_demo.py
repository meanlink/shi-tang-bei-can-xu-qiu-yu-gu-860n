import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, Dish, MealBatch, Meal
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services import meals
from src.constants.allergens import DEFAULT_ALLERGEN_VERSION
from src.schemas.meal import BatchInput, ConfigureInput, BatchDishInput, ComboInput
from src.utils.security import hash_password
from src.utils.serialization import checksum

DEMO_DISHES = [
    # code, name, allergens, label_status, default_portions
    ('D01', '番茄炒蛋盖饭', ['egg'], 'known', 120),
    ('D02', '宫保鸡丁饭', ['peanut', 'soy', 'gluten'], 'known', 110),
    ('D03', '清蒸鲈鱼配米饭', ['fish'], 'known', 90),
    ('D04', '菌菇豆腐煲', ['soy'], 'known', 80),
    ('D05', '香煎鸡排配时蔬', ['gluten', 'milk'], 'known', 100),
    ('D06', '素什锦米粉', ['gluten'], 'known', 70),
    ('D07', '南瓜藜麦饭碗', [], 'known', 60),
    ('D08', '红烧牛肉面', ['gluten', 'soy', 'celery'], 'unknown', 50),
]


def _seed_dishes(db):
    created = []
    for code, name, allergens, status, portions in DEMO_DISHES:
        existing = db.scalar(select(Dish).where(Dish.code == code))
        if existing:
            created.append(existing)
            continue
        dish = Dish(
            code=code, name=name, allergens=allergens,
            allergen_version=DEFAULT_ALLERGEN_VERSION, label_status=status,
            default_portions=portions, active=True, created_at=time.time(),
        )
        db.add(dish)
        db.flush()
        created.append(dish)
    return created


def _seed_batch(db):
    admin = db.scalar(select(User).where(User.role == 'admin'))
    job = db.scalar(select(Job).where(Job.approved == True).order_by(Job.id))
    if not job:
        return
    dishes = {d.code: d for d in db.scalars(select(Dish))}
    if not db.scalar(select(MealBatch).where(MealBatch.name == '11月9日午餐过敏餐配餐')):
        first_date = job.result['table']['rows'][0]['date']
        batch = meals.create_batch(
            db,
            BatchInput(name='11月9日午餐过敏餐配餐', job_id=job.id, date=first_date, allergen_version=DEFAULT_ALLERGEN_VERSION),
            admin,
        )
        configure = ConfigureInput(
            dishes=[
                BatchDishInput(dish_id=dishes['D01'].id, portions=120),
                BatchDishInput(dish_id=dishes['D02'].id, portions=110),
                BatchDishInput(dish_id=dishes['D03'].id, portions=90),
                BatchDishInput(dish_id=dishes['D04'].id, portions=80),
                BatchDishInput(dish_id=dishes['D05'].id, portions=100),
                BatchDishInput(dish_id=dishes['D06'].id, portions=70),
                BatchDishInput(dish_id=dishes['D07'].id, portions=60),
                BatchDishInput(dish_id=dishes['D08'].id, portions=50),
            ],
            combos=[
                ComboInput(name='乳制品忌口', exclusions=['milk'], count=12),
                ComboInput(name='麸质与大豆忌口', exclusions=['gluten', 'soy'], count=8),
                ComboInput(name='花生与坚果忌口', exclusions=['peanut', 'tree_nut'], count=6),
            ],
        )
        meals.configure_batch(db, batch, configure, admin)
        meals.allocate(db, batch, None, admin)
        db.add(Audit(actor='seed', action='demo.allergen-batch', detail={'batch': batch.id, 'status': batch.status}, at=time.time()))


def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        _seed_dishes(db)
        _seed_batch(db)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,Dish,MealBatch,Meal]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
