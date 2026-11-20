import { jobs, mealCatalog, dishes, mealBatches } from '../api/entities.js';
import { downloadMealDispatch } from '../api/client.js';
import { workspace } from '../stores/workspace.js';
import { session } from '../stores/session.js';
import { heading } from '../components/common/shell.js';
import { statusChip, fulfillmentBar, basisRow, dishChip } from '../components/common/meal.js';
import { batchStatus, mealStatus } from '../constants/enums.js';
import { esc, date, notify } from '../utils/dom.js';
import { submit } from '../hooks/forms.js';

let selectedBatch = null;
let filter = { combo: 'all', status: 'all', query: '' };
let showDishes = false;

const canWrite = () => ['admin', 'analyst'].includes(session.user?.role);
const isAdmin = () => session.user?.role === 'admin';

const allergenOptions = (catalog, selected = []) =>
  catalog.allergens.map(a => '<label class="check"><input type="checkbox" name="allergen" value="' + esc(a.code) + '" ' + (selected.includes(a.code) ? 'checked' : '') + '>' + esc(a.label) + '</label>').join('');

function plannedDays(jobList) {
  const days = [];
  for (const j of jobList) {
    if (!j.approved || j.status !== 'completed') continue;
    for (const row of j.result?.table?.rows || []) {
      days.push({ job: j, date: row.date, plan: row.plan, forecast: row.forecast, buffer: row.buffer });
    }
  }
  return days.sort((a, b) => a.date.localeCompare(b.date));
}

export async function mealPage(id) {
  const [catalog, dishList, batchList, jobList] = await Promise.all([
    mealCatalog(), dishes.list(), mealBatches.list(), jobs.list(),
  ]);
  workspace.mealCatalog = catalog;
  workspace.dishes = dishList;
  workspace.mealBatches = batchList;
  if (id) selectedBatch = Number(id);
  if (!selectedBatch || !batchList.some(b => b.id === selectedBatch)) selectedBatch = batchList[0]?.id || null;
  const detail = selectedBatch ? await mealBatches.get(selectedBatch) : null;
  workspace.mealDetail = detail;
  const m = workspace.meta.meal_allocation || {};

  const days = plannedDays(jobList);

  return heading('06 / ALLERGEN KITCHEN', m.title || '过敏餐配餐台', m.tagline || '按已批准份数配过敏餐，配齐才整批下发。',
    '<button class="button" id="toggle-dishes">' + (showDishes ? '收起菜品目录' : '菜品与标签目录') + '</button>') +
    '<div class="meal-grid">' +
    '<section class="panel batch-panel">' +
      '<div class="section-title"><h2>配餐批次</h2><span>' + batchList.length + ' 批 · 锁定过敏原版本</span></div>' +
      '<div class="batch-list">' + (batchList.length ? batchList.map(b =>
        '<button class="batch-item ' + (b.id === selectedBatch ? 'active' : '') + '" data-batch="' + b.id + '">' +
        '<strong>' + esc(b.name) + '</strong>' +
        '<span>' + esc(b.date) + ' · ' + b.total + ' 份 · v' + b.version + '</span>' +
        '<small>' + statusChip(batchStatus, b.status) + ' · 版本 ' + esc(b.allergen_version) + '</small>' +
        '</button>').join('') : '<p class="empty">尚无配餐批次，请先在右侧按已批准份数建立。</p>') + '</div>' +
      '<form id="batch-form">' +
        '<div class="section-title"><h2>新建每日配餐</h2><span>份数只取自已批准分析</span></div>' +
        '<label>批次名称<input name="name" required minlength="2" value="' + esc((days[0]?.date || '') + ' 午餐过敏餐配餐') + '"></label>' +
        '<label>已批准每日份数<select name="plan" required>' + days.map(d =>
          '<option value="' + d.job.id + '|' + esc(d.date) + '">' + esc(d.date) + ' · ' + d.plan + ' 份 · ' + esc(d.job.name) + '</option>').join('') + '</select></label>' +
        '<label>过敏原版本<input name="version" value="' + esc(detail?.allergen_version || catalog.default_version || '2026.11') + '" required maxlength="40"></label>' +
        '<label>备注<input name="note" maxlength="500" placeholder="例如：园区上午活动，取餐提前 15 分钟"></label>' +
        '<button type="submit" class="primary" ' + (!canWrite() || !days.length ? 'disabled' : '') + '>建立批次</button>' +
        '<p class="hint">每日份数来自已批准的结果复核表；批次建立后总数固定，份数变更须新建批次或登记替料。</p>' +
      '</form>' +
    '</section>' +
    '<section class="panel detail-panel">' + (detail ? renderDetail(detail, catalog) : '<p class="empty">请选择一个批次，或先建立批次。</p>') + '</section>' +
    '</div>' +
    (showDishes ? renderDishes(dishList, catalog) : '');
}

function renderDetail(b, catalog) {
  const recipes = workspace.meta.meal_allocation?.rules || [];
  const allowed = b.status !== 'dispatched' && b.status !== 'allocating' && !b.dispatches?.length;
  const mealRows = b.meals || [];
  const combos = b.combos || [];
  const dishTotal = (b.dishes || []).reduce((s, x) => s + x.portions, 0);
  return '<div class="detail-head"><div><p class="eyebrow">' + esc(b.date) + ' · ' + esc(b.allergen_version) + '</p><h2>' + esc(b.name) + '</h2></div>' + statusChip(batchStatus, b.status) + '</div>' +
    '<div class="metric-row">' +
      '<div><span>已批准份数</span><strong>' + b.total + '</strong></div>' +
      '<div><span>登记菜品容量</span><strong>' + dishTotal + '</strong></div>' +
      '<div><span>配餐版本</span><strong>v' + b.version + '</strong></div>' +
      '<div><span>已下发 / 出餐</span><strong>' + b.progress.dispatched_meals + ' / ' + b.progress.produced_meals + '</strong></div>' +
    '</div>' +
    fulfillmentBar(b.meal_counts, b.total) +
    (b.status === 'short' ? shortagePanel(b) : '') +
    (b.status === 'allocating' ? '<div class="warning">配餐曾中断。登记份数只读，按原批恢复后重试不会重复占用菜品。<button class="button small" data-recover="' + b.id + '">恢复原批</button></div>' : '') +
    '<div class="action-row">' +
      '<button class="button primary" data-allocate="' + b.id + '" data-version="' + b.version + '" ' + (!canWrite() || b.status === 'allocating' ? 'disabled' : '') + '>重新配餐</button>' +
      '<button class="button" data-dispatch="' + b.id + '" data-version="' + b.version + '" ' + (!isAdmin() || b.status !== 'ready' ? 'disabled' : '') + '>整批下发</button>' +
      '<button class="button" data-show-sub="' + b.id + '" ' + (!canWrite() ? 'disabled' : '') + '>登记替料</button>' +
      '<a class="button" href="#/meals">刷新 ↻</a>' +
    '</div>' +
    '<p class="hint">' + esc(recipes.join(' ')) + '</p>' +
    '<div class="section-title"><h2>忌口组合与份数</h2><span>合计 ' + combos.reduce((s, c) => s + c.count, 0) + ' / ' + b.total + '</span></div>' +
    '<div class="combo-grid">' + combos.map(c => '<div class="combo-card"><strong>' + esc(c.name) + '</strong><span>' + c.count + ' 份</span><small>' + ((c.exclusion_labels || []).map(esc).join('、') || '无特殊忌口') + '</small></div>').join('') + '</div>' +
    (allowed ? renderConfigure(b, catalog) : '<p class="hint">该批次已有下发或出餐记录，登记已锁定；调整请使用替料。</p>') +
    renderSubstitutions(b) +
    renderDispatches(b) +
    '<div class="section-title"><h2>逐份核对用料依据</h2><span>' + mealRows.length + ' 份</span></div>' +
    renderMealFilters(b) +
    '<div class="meal-list">' + mealRows.map(m => {
      const matchCombo = filter.combo === 'all' || String(m.combo_id) === filter.combo;
      const matchStatus = filter.status === 'all' || m.status === filter.status;
      const q = filter.query.trim();
      const matchQuery = !q || String(m.seq) === q || (m.dish_name || '').includes(q) || (m.combo_name || '').includes(q);
      if (!(matchCombo && matchStatus && matchQuery)) return '';
      return '<article class="meal-row">' + basisRow(m) +
        (m.status === 'dispatched' && canWrite() ? '<button class="text-button" data-produce="' + m.id + '" data-batch="' + b.id + '">标记出餐</button>' : '') +
        '</article>';
    }).join('') + '</div>';
}

function shortagePanel(b) {
  const s = b.shortage || {};
  const combos = (s.combos || []).map(c => '<li><strong>' + esc(c.name) + '</strong> 需 ' + c.need + ' · 已配 ' + c.filled + ' · <b>缺 ' + c.missing + '</b></li>').join('');
  const paused = (s.paused_dishes || []).map(p => '<li>' + esc(p.name) + ' · ' + esc(p.reason) + '（登记 ' + p.portions + ' 份未参与分配）</li>').join('');
  const capacity = (s.capacity || []).filter(c => c.available <= 0).map(c => '<li>' + esc(c.name) + ' 可用 0 份（登记 ' + c.registered + '，已占用 ' + c.locked + '）</li>').join('');
  return '<div class="warning short-panel"><strong>整批待补：' + (s.missing ?? '—') + ' 份未配齐，配齐前不会下发。</strong>' +
    '<ul>' + combos + paused + capacity + '</ul><p>' + esc(s.hint || '可补充菜品份数或登记替料后重新配餐。') + '</p></div>';
}

function renderConfigure(b, catalog) {
  const dList = workspace.dishes || [];
  return '<details class="config-block"' + (b.status === 'pending' && !b.combos?.length ? ' open' : '') + '><summary>登记忌口组合与菜品份数</summary>' +
    '<form id="configure-form">' +
      '<div class="combo-editor"><div class="section-title"><h2>忌口组合</h2><span>需与当日份数一致</span></div>' +
      '<div id="combo-rows">' + (b.combos?.length ? b.combos.filter(c => !c.name.startsWith('普通餐')).map(c =>
        comboRow(c.name, c.exclusions, c.count, catalog)).join('') : comboRow('', [], 0, catalog)) + '</div>' +
      '<button type="button" class="button small" id="add-combo">+ 增加组合</button></div>' +
      '<div class="dish-editor"><div class="section-title"><h2>菜品份数</h2><span>标签不明或版本不符会自动暂停</span></div>' +
      '<div class="dish-rows">' + dList.map(d => {
        const reg = (b.dishes || []).find(x => x.dish_id === d.id);
        return '<label class="dish-row"><span>' + esc(d.name) + ' <small>' + esc(d.code) + ' · ' + esc(d.allergen_version) + '</small></span>' +
          '<span class="tag">' + dishChip(d) + '</span>' +
          '<input type="number" min="0" max="5000" name="dish-' + d.id + '" value="' + (reg ? reg.portions : 0) + '"></label>';
      }).join('') + '</div></div>' +
      '<button type="submit" class="primary" ' + (!canWrite() ? 'disabled' : '') + '>保存登记</button>' +
      '<p class="hint">组合合计等于当日份数：差额自动记为「普通餐（无特殊忌口）」。菜品份数只读登记，配餐与重试不会递减。</p>' +
    '</form></details>';
}

function comboRow(name, exclusions, count, catalog) {
  return '<div class="combo-row"><input name="combo-name" placeholder="组合名称" value="' + esc(name) + '">' +
    '<input name="combo-count" type="number" min="0" value="' + (count || 0) + '">' +
    '<div class="checks">' + allergenOptions(catalog, exclusions) + '</div>' +
    '<button type="button" class="text-button remove-combo">移除</button></div>';
}

function renderSubstitutions(b) {
  const dishOptions = (b.dishes || []).map(x => '<option value="' + x.dish_id + '">' + esc(x.dish?.name || x.dish_id) + ' · 登记 ' + x.portions + ' 份</option>').join('');
  const allDishes = (workspace.dishes || []).map(d => '<option value="' + d.id + '">' + esc(d.name) + '</option>').join('');
  return '<details class="config-block" id="sub-block"><summary>替料登记（挂起受影响未出餐份）</summary>' +
    '<form id="sub-form">' +
      '<div class="parameter-grid">' +
        '<label>替出菜品<select name="from_dish_id" required>' + dishOptions + '</select></label>' +
        '<label>替入菜品<select name="to_dish_id" required>' + allDishes + '</select></label>' +
        '<label>替料份数<input name="count" type="number" min="1" value="1" required></label>' +
        '<label>原因<input name="reason" required minlength="2" placeholder="例如：蛋类到货延迟"></label>' +
      '</div>' +
      '<button type="submit" class="primary" ' + (!canWrite() ? 'disabled' : '') + '>登记替料并重配</button>' +
      '<p class="hint">替入菜品须标签明确、版本一致且不违反受影响份的忌口；已下发未出餐份会挂起重配，已出餐份保留原标签。</p>' +
    '</form>' +
    ((b.substitutions || []).length ? '<div class="sub-list">' + b.substitutions.map(s =>
      '<div class="sub-item"><strong>' + esc(s.from_dish) + ' → ' + esc(s.to_dish) + ' · ' + s.count + ' 份</strong>' +
      '<small>' + esc(s.reason) + ' · 挂起 ' + s.affected.length + ' 份 · v' + s.version_after + ' · ' + esc(s.actor) + '</small></div>').join('') + '</div>' : '') +
    '</details>';
}

function renderDispatches(b) {
  const rows = b.dispatches || [];
  return '<div class="section-title"><h2>下发单</h2><span>' + rows.length + ' 份不可变标签快照</span></div>' +
    (rows.length ? rows.map(d => '<div class="delivery"><div><strong>' + esc(d.filename) + '</strong><p>' + d.total + ' 份 · 版本 ' + esc(d.allergen_version) + ' · ' + date(d.created_at) + '</p><small>SHA256 ' + esc(d.checksum.slice(0, 24)) + '…</small></div>' +
      '<div><button class="text-button" data-meal-download="' + d.id + '" data-format="csv">CSV ↓</button><button class="text-button" data-meal-download="' + d.id + '" data-format="json">JSON ↓</button></div></div>').join('') :
      '<p class="empty">配齐并整批下发后，这里保存每份的标签与用料依据。</p>');
}

function renderMealFilters(b) {
  const comboOptions = (b.combos || []).map(c => '<option value="' + c.id + '" ' + (filter.combo === String(c.id) ? 'selected' : '') + '>' + esc(c.name) + '</option>').join('');
  const statusOptions = Object.entries(mealStatus).map(([k, label]) => '<option value="' + k + '" ' + (filter.status === k ? 'selected' : '') + '>' + label + '</option>').join('');
  return '<div class="meal-filters">' +
    '<label>组合<select id="filter-combo"><option value="all">全部组合</option>' + comboOptions + '</select></label>' +
    '<label>状态<select id="filter-status"><option value="all">全部状态</option>' + statusOptions + '</select></label>' +
    '<label>查找<input id="filter-query" value="' + esc(filter.query) + '" placeholder="份号 / 菜品 / 组合"></label>' +
    '</div>';
}

function renderDishes(dishList, catalog) {
  return '<section class="panel"><div class="section-title"><h2>菜品与过敏原标签</h2><span>' + dishList.length + ' 道 · 标签版本 ' + esc(workspace.meta.meal_allocation?.version || catalog.default_version || '2026.11') + '</span></div>' +
    '<div class="dish-catalog">' + dishList.map(d => '<article class="dish-card"><div><strong>' + esc(d.name) + '</strong><small>' + esc(d.code) + '</small></div>' +
      '<p>' + ((d.allergen_labels || []).map(esc).join('、') || '无已知过敏原') + '</p>' + dishChip(d) +
      '<button class="text-button" data-edit-dish="' + d.id + '">修正标签</button></article>').join('') + '</div>' +
    '<form id="dish-form" class="inline-dish-form"><div class="parameter-grid">' +
      '<label>编码<input name="code" required minlength="2" placeholder="D09"></label>' +
      '<label>菜品<input name="name" required minlength="2"></label>' +
      '<label>过敏原版本<input name="allergen_version" value="' + esc(catalog.default_version || '2026.11') + '"></label>' +
      '<label>默认份数<input name="default_portions" type="number" min="0" value="0"></label>' +
    '</div><div class="checks">' + allergenOptions(catalog) + '</div>' +
    '<label class="check"><input type="checkbox" name="unknown">标签不明（暂停分配）</label>' +
    '<button type="submit" class="primary" ' + (!canWrite() ? 'disabled' : '') + '>新增菜品</button></form></section>';
}

function collectCombos() {
  const rows = [...document.querySelectorAll('#combo-rows .combo-row')];
  return rows.map(row => ({
    name: row.querySelector('[name=combo-name]').value.trim(),
    count: Number(row.querySelector('[name=combo-count]').value),
    exclusions: [...row.querySelectorAll('[name=allergen]:checked')].map(x => x.value),
  })).filter(c => c.name && c.count > 0);
}

function collectDishes() {
  return (workspace.dishes || []).map(d => ({
    dish_id: d.id,
    portions: Number(document.querySelector('[name="dish-' + d.id + '"]')?.value || 0),
  })).filter(x => x.portions > 0);
}

export function bindMeals(refresh) {
  document.querySelectorAll('[data-batch]').forEach(b => b.onclick = () => { selectedBatch = Number(b.dataset.batch); filter = { combo: 'all', status: 'all', query: '' }; location.hash = '#/meals'; refresh(); });
  document.querySelector('#toggle-dishes')?.addEventListener('click', async () => { showDishes = !showDishes; await refresh(); });

  const batchForm = document.querySelector('#batch-form');
  if (batchForm) submit(batchForm, async f => {
    const [jobId, day] = String(f.get('plan')).split('|');
    const created = await mealBatches.create({ name: f.get('name'), job_id: Number(jobId), date: day, allergen_version: f.get('version'), note: f.get('note') });
    notify('批次已建立，请登记忌口组合与菜品份数');
    selectedBatch = created.id; location.hash = '#/meals'; await refresh();
  });

  document.querySelector('#add-combo')?.addEventListener('click', () => {
    const rows = document.querySelector('#combo-rows');
    rows.insertAdjacentHTML('beforeend', comboRow('', [], 0, workspace.mealCatalog));
    rows.querySelectorAll('.remove-combo').forEach(btn => btn.onclick = () => btn.closest('.combo-row').remove());
  });
  document.querySelectorAll('.remove-combo').forEach(btn => btn.onclick = () => btn.closest('.combo-row').remove());

  const configForm = document.querySelector('#configure-form');
  if (configForm) submit(configForm, async () => {
    const detail = workspace.mealDetail;
    const batch = await mealBatches.configure(detail.id, {
      expected_version: detail.version,
      dishes: collectDishes(),
      combos: collectCombos(),
    });
    notify('登记已保存，可以重新配餐');
    await refresh();
  });

  document.querySelectorAll('[data-allocate]').forEach(b => b.onclick = async () => {
    try {
      const r = await mealBatches.allocate(b.dataset.allocate, { expected_version: Number(b.dataset.version) });
      notify(r.batch.status === 'short' ? '未配齐：整批待补，未下发' : '已配齐，可以整批下发');
      await refresh();
    } catch (e) { notify(e.message, true); }
  });

  document.querySelectorAll('[data-dispatch]').forEach(b => b.onclick = async () => {
    try {
      await mealBatches.dispatch(b.dataset.dispatch, { expected_version: Number(b.dataset.version) });
      notify('已整批下发，标签快照已归档');
      await refresh();
    } catch (e) { notify(e.message, true); await refresh(); }
  });

  document.querySelectorAll('[data-recover]').forEach(b => b.onclick = async () => {
    try { await mealBatches.recover(b.dataset.recover); notify('已按原批恢复，可重新配餐'); await refresh(); }
    catch (e) { notify(e.message, true); }
  });

  document.querySelector('[data-show-sub]')?.addEventListener('click', () => {
    const block = document.querySelector('#sub-block');
    if (block) { block.open = true; block.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
  });

  const subForm = document.querySelector('#sub-form');
  if (subForm) submit(subForm, async f => {
    const detail = workspace.mealDetail;
    await mealBatches.substitute(detail.id, {
      from_dish_id: Number(f.get('from_dish_id')),
      to_dish_id: Number(f.get('to_dish_id')),
      count: Number(f.get('count')),
      reason: f.get('reason'),
      expected_version: detail.version,
    });
    notify('替料已登记，受影响未出餐份已挂起重配');
    await refresh();
  });

  document.querySelectorAll('[data-produce]').forEach(b => b.onclick = async () => {
    try { await mealBatches.produce(b.dataset.batch, b.dataset.produce); notify('已标记出餐，保留原标签'); await refresh(); }
    catch (e) { notify(e.message, true); }
  });

  document.querySelectorAll('[data-meal-download]').forEach(b => b.onclick = () => downloadMealDispatch(b.dataset.mealDownload, b.dataset.format).catch(e => notify(e.message, true)));

  const fCombo = document.querySelector('#filter-combo');
  if (fCombo) fCombo.onchange = async () => { filter.combo = fCombo.value; await refresh(); };
  const fStatus = document.querySelector('#filter-status');
  if (fStatus) fStatus.onchange = async () => { filter.status = fStatus.value; await refresh(); };
  const fQuery = document.querySelector('#filter-query');
  if (fQuery) {
    let timer;
    fQuery.oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        filter.query = fQuery.value;
        // 保留输入焦点与光标，避免重建整页后丢失输入
        const pos = fQuery.selectionStart;
        await refresh();
        const next = document.querySelector('#filter-query');
        if (next) { next.focus(); next.setSelectionRange(pos, pos); }
      }, 350);
    };
  }

  document.querySelectorAll('[data-edit-dish]').forEach(b => b.onclick = async () => {
    const d = (workspace.dishes || []).find(x => String(x.id) === b.dataset.editDish);
    if (!d || !canWrite()) return;
    const name = prompt('菜品名称', d.name);
    if (name === null) return;
    const codeList = prompt('过敏原代码（逗号分隔，留空表示无）', (d.allergens || []).join(','));
    if (codeList === null) return;
    const version = prompt('过敏原标签版本', d.allergen_version);
    if (version === null) return;
    const allergens = codeList.split(',').map(s => s.trim()).filter(Boolean);
    try {
      await dishes.patch(d.id, { name, allergens, allergen_version: version });
      notify('标签已更新。若影响已配批次，下发时会被阻止并要求重配');
      await refresh();
    } catch (e) { notify(e.message, true); }
  });

  const dishForm = document.querySelector('#dish-form');
  if (dishForm) submit(dishForm, async f => {
    await dishes.create({
      code: f.get('code'), name: f.get('name'),
      allergens: [...dishForm.querySelectorAll('[name=allergen]:checked')].map(x => x.value),
      allergen_version: f.get('allergen_version'),
      label_status: f.get('unknown') ? 'unknown' : 'known',
      default_portions: Number(f.get('default_portions')),
    });
    notify('菜品已加入目录');
    await refresh();
  });
}
