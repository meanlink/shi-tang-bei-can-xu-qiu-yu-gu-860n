import { dishStatus, mealStatus } from '../../constants/enums.js';
import { esc } from '../../utils/dom.js';

export const statusChip = (map, value) => '<span class="status meal-status ' + esc(value) + '">' + esc(map[value] || value) + '</span>';

export function dishChip(dish) {
  if (!dish) return '<span class="status">未知菜品</span>';
  return statusChip(dishStatus, dish.label_status) + '<span class="version">标签 v' + esc(dish.allergen_version) + '</span>';
}

export function fulfillmentBar(mealCounts, total) {
  const safeTotal = Math.max(1, total || 1);
  const parts = [
    ['dispatched', '已下发', 'ok'],
    ['produced', '已出餐', 'done'],
    ['allocated', '已配齐', 'ready'],
    ['held', '挂起', 'warn'],
    ['pending', '待分配', 'idle'],
  ];
  const bars = parts.filter(([k]) => (mealCounts[k] || 0) > 0)
    .map(([k, label, cls]) => '<span class="bar ' + cls + '" style="width:' + ((mealCounts[k] || 0) / safeTotal * 100) + '%" title="' + label + ' ' + mealCounts[k] + ' 份"></span>');
  return '<div class="fulfillment"><div class="bar-track">' + bars.join('') + '</div><div class="bar-key">' + parts.map(([k, label]) => '<span><i class="dot ' + k + '"></i>' + label + ' ' + (mealCounts[k] || 0) + '</span>').join('') + '</div></div>';
}

export function basisRow(meal) {
  const basis = meal.basis || {};
  const dish = basis.dish || {};
  const combo = basis.combo || {};
  const exclusions = (combo.exclusion_labels || []).map(esc).join('、') || '无特殊忌口';
  const allergens = (dish.allergen_labels || []).map(esc).join('、') || '无已知过敏原';
  const hasBasis = Boolean(meal.basis && (basis.dish || basis.combo));
  return '<details class="basis"><summary>第 ' + meal.seq + ' 份 · ' + esc(combo.name || '未登记组合') + ' → ' + esc(meal.dish_name || '待分配') + '</summary>' +
    '<div class="basis-grid">' +
    '<div><span>忌口组合</span><strong>' + esc(combo.name || '—') + '</strong><small>' + exclusions + '</small></div>' +
    '<div><span>菜品标签版本</span><strong>' + esc(dish.allergen_version || '—') + '</strong><small>' + esc(mealStatus[meal.status] || meal.status) + '</small></div>' +
    '<div><span>菜品过敏原</span><strong>' + allergens + '</strong><small>SHA ' + esc(String(dish.label_hash || '').slice(0, 12)) + '…</small></div>' +
    '<div><span>分配依据</span><strong>' + esc(basis.rule || '—') + '</strong><small>登记份数 ' + esc(basis.registered_portions ?? '—') + '</small></div>' +
    '</div>' +
    (hasBasis ? '<p class="hint">批次锁定过敏原版本 ' + esc(basis.allergen_version || '—') + '，配餐版本 v' + esc(basis.batch_version || '—') + '。</p>' : '<p class="hint">该份尚未生成标签依据。</p>') +
    (meal.label_history?.length ? '<div class="history">' + meal.label_history.slice(-3).map(h => '<span>' + esc(h.reason || (h.dispatched_at ? '下发' : '更新')) + ' · ' + esc((h.previous || h.label || {}).dish_name || '') + '</span>').join('') + '</div>' : '') +
    '</details>';
}
