import {fileIndex, heading, recordHeader, stamp} from '../components.js';
import {materials, packings, recheckStates, reviewStates, routes} from '../constants.js';
import {dateTime, escape as e, percent} from '../format.js';

const riskLabels = {low: '低风险', high: '高风险'};

function signedPercent(value) {
  const rendered = percent(Math.abs(value ?? 0));
  return `${value > 0 ? '+' : value < 0 ? '-' : '±'}${rendered}`;
}

function riskResult(result) {
  const evidence = result.evidence;
  const snapshot = result.input_snapshot;
  const p = result.probability;
  const threshold = evidence.risk_threshold;
  return `<div class="scoring-sheet"><section class="scoring-slip">
    <h3>模型判定 ${stamp(result.risk, riskLabels)}</h3>
    <div class="probability">${percent(p)}<small>模型风险概率</small></div>
    <div class="risk-bar" role="img" aria-label="风险 ${percent(p)}，阈值 ${percent(threshold)}"><i style="width:${p * 100}%"></i>
      <b style="left:${threshold * 100}%"></b></div><div class="risk-labels"><span>0%</span><span>复核阈值 ${percent(threshold)}</span><span>100%</span></div>
    <p>${result.risk === 'high' ? '已生成开箱复核单。' : '风险环节已放行。派车仍需确认封签与窗口。'}</p></section>
    <section class="feature-sheet"><h3>包装输入快照</h3><dl class="facts">
      <dt>器物材质</dt><dd>${e(materials[snapshot.material])}</dd><dt>包装结构</dt><dd>${e(packings[snapshot.packing])}</dd>
      <dt>路线类别</dt><dd>${e(routes[snapshot.route_type])}</dd><dt>最高相对湿度</dt><dd>${snapshot.max_rh}% RH</dd>
      <dt>转运次数</dt><dd>${snapshot.transfers} 次</dd></dl></section></div>
    <section class="evidence"><div><h3>模型留档</h3><p>${e(evidence.model_version)} · ${dateTime(result.computed_at)} · 合成样本</p>
      <dl class="facts"><dt>训练 / 留出</dt><dd>${evidence.train_count} / ${evidence.test_count}</dd><dt>ROC AUC</dt><dd>${evidence.roc_auc.toFixed(3)}</dd>
      <dt>Brier 分数</dt><dd>${evidence.brier_score.toFixed(3)}</dd><dt>随机种子</dt><dd>${evidence.random_seed}</dd>
      <dt>模型指纹</dt><dd class="mono">${evidence.model_sha256 ? `${e(evidence.model_sha256.slice(0, 16))}…` : '未留档'}</dd></dl></div>
      <details><summary>查看编码与输入指纹</summary><dl class="facts">${Object.entries(evidence.encoded_features).map(([label, value]) =>
        `<dt>${e(label)}</dt><dd>${value.toFixed(4)}</dd>`).join('')}</dl><p class="fingerprint mono">${e(evidence.input_sha256)}</p></details></section>`;
}

function recheckPanel(record, staff) {
  const result = record.triage;
  const recheck = record.recheck;
  if (result?.status !== 'scored') return '';
  const supervisor = staff.role === 'supervisor';
  const startable = !recheck || recheck.status === 'failed' || recheck.status === 'unverifiable';
  let body;
  if (!recheck) {
    body = '<p class="empty">尚未复算。理赔需要时，由主管用留档模型核对封存风险分。</p>';
  } else if (recheck.status === 'queued') {
    body = '<p class="empty">复算排队中，完成后本页自动更新对照结果。</p>';
  } else if (recheck.status === 'unverifiable') {
    body = `<p class="alert">无法核对：该档案评分时未留存模型，没有原版模型可供复算。</p>
      <dl class="facts"><dt>复算留痕</dt><dd>${e(recheck.requested_by)} 发起 · ${dateTime(recheck.computed_at)} 标记</dd></dl>`;
  } else if (recheck.status === 'failed') {
    body = `<p class="alert">${e(recheck.error ?? '复算计算失败，可重试。')}</p>`;
  } else {
    body = `<dl class="facts">
      <dt>封存概率</dt><dd>${percent(result.probability)}<small>（${dateTime(result.computed_at)} 归档）</small></dd>
      <dt>复算概率</dt><dd>${percent(recheck.probability)}<small>（差 ${signedPercent(recheck.probability_delta)}）</small></dd>
      <dt>封存判定</dt><dd>${stamp(result.risk, riskLabels)}</dd>
      <dt>复算判定</dt><dd>${stamp(recheck.risk, riskLabels)}</dd>
      <dt>开箱判定</dt><dd>${e(recheck.review_status ? reviewStates[recheck.review_status] : '无开箱复核单')}</dd>
      <dt>模型留档</dt><dd>${e(result.evidence.model_version)} · 指纹 <span class="mono">${e(recheck.model_sha256.slice(0, 12))}…</span></dd>
      <dt>复算留痕</dt><dd>${e(recheck.requested_by)} 发起 · ${dateTime(recheck.computed_at)} 完成</dd></dl>
    <p class="${recheck.risk_changed ? 'alert' : 'success-line'}">${recheck.risk_changed
      ? '复算判定与封存判定不一致，请结合开箱记录复核。' : '复算概率与判定同封存档案一致。'}</p>`;
  }
  const action = supervisor && startable ? `<form data-action="recheck"><div class="form-actions">
    <span>${recheck ? '复算未得出核对结论，可重新发起。' : '复算使用留档模型与原包装快照，原派车档案保持封存。'}</span>
    <button>${recheck ? '重新复算' : '发起理赔复算'}</button></div></form>` : '';
  return `<section class="recheck-panel"><div class="section-line"><h3>理赔复算</h3>
    ${recheck ? stamp(recheck.status, recheckStates) : ''}</div>${body}${action}</section>`;
}

export function triagePage({loans, record, staff}) {
  const result = record?.triage;
  return `${heading('风险分流', '以包装快照确定是否安排开箱复核')}
    <div class="workbench">${fileIndex(loans, record, 'triage')}${record ? `<article class="dossier">${recordHeader(record)}
    ${result?.status === 'scored' ? riskResult(result) + recheckPanel(record, staff) : `<section class="triage-intake"><h3>${result?.status === 'queued' ? '风险分流排队中' : '提交包装记录'}</h3>
      <p>${e(result?.error ?? (result?.status === 'queued' ? '模型完成后，本页会更新判定与复核任务。' : '先完成包装检查，再将材质、包装、路线、湿度与转运次数归档计算。'))}</p>
      ${staff.role === 'operator' && record.crate && result?.status !== 'queued' ? `<form data-action="triage"><button>${result?.status === 'failed' ? '重试分流' : '提交风险分流'}</button></form>` : ''}</section>`}
    </article>` : ''}</div>`;
}
