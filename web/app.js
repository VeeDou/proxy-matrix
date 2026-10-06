let state = null;
let activeJob = null;
let page = 'overview';
const pendingUrlInputs = {};
const $ = (id) => document.getElementById(id);
const names = { overview: '概览', subscriptions: '机场订阅', sites: '网站与 App', tests: '测试', sync: '生成与应用' };
const taskNames = { check: '配置校验', google: 'Google 出口测试', ai: 'AI 端点探测', website: '网站分流测试', subscription: '订阅测试', publish: '生成并应用配置', refresh_providers: '刷新机场节点' };

let editBaseRev = null;
let editActionEpoch = 0;
let remoteDraftConflict = false;
let backgroundPollInFlight = false;

const lastRefreshErrors = {};
const expandedDates = new Set();
let pollTimeoutId = null;
let pollActive = false;

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (s) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[s]));
}

function formatRelative(isoStr) {
  const ms = Date.now() - new Date(isoStr).getTime();
  if (isNaN(ms) || ms < 0) return '刚刚';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return '刚刚';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  return `${day} 天前`;
}

function formatExact(isoStr) {
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    return d.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' }) + ' (北京时间)';
  } catch {
    return isoStr;
  }
}

function formatNextEstimate(nextMs) {
  try {
    const now = new Date();
    const d = new Date(nextMs);
    if (isNaN(d.getTime())) return '计划时间内';
    const isToday = d.toDateString() === now.toDateString();
    const isTomorrow = new Date(now.getTime() + 86400000).toDateString() === d.toDateString();
    const timeStr = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' });
    if (isToday) return `今天约 ${timeStr}（电脑开机时）`;
    if (isTomorrow) return `明天约 ${timeStr}（电脑开机时）`;
    return `约 ${d.getMonth() + 1}月${d.getDate()}日 ${timeStr}（电脑开机时）`;
  } catch {
    return '计划时间内';
  }
}

function formatInterval(intervalSec) {
  if (intervalSec === 0) return '手动更新';
  if (intervalSec < 3600) {
    const mins = Math.max(1, Math.round(intervalSec / 60));
    return `每 ${mins} 分钟`;
  }
  const hrs = intervalSec / 3600;
  if (hrs % 1 === 0) {
    return `每 ${hrs} 小时`;
  }
  return `每 ${hrs.toFixed(1)} 小时`;
}

function computeSubTiming(sub, liveConnected) {
  const err = lastRefreshErrors[sub.name];
  const rawInterval = sub.interval;
  const intervalSec = (rawInterval !== undefined && rawInterval !== null && !isNaN(rawInterval) && rawInterval !== '') ? Number(rawInterval) : 86400;
  const intervalText = formatInterval(intervalSec);

  if (!liveConnected) {
    return {
      badgeText: '⚪ 内核未连接',
      badgeClass: 'muted',
      timingText: '内核未运行',
      intervalText: intervalText,
      nextText: '—',
      fullTime: '内核未连接，无法读取更新时间',
      guidance: 'Clash Verge 内核未运行'
    };
  }
  if (!sub.in_kernel) {
    return {
      badgeText: '⚪ 尚未载入内核',
      badgeClass: 'amber',
      timingText: '待客户端重载',
      intervalText: intervalText,
      nextText: '—',
      fullTime: '尚未在运行中的内核加载',
      guidance: '该机场尚未应用到内核，请先点击【应用修改】'
    };
  }
  if (sub.changed) {
    return {
      badgeText: '⚠️ 订阅地址已改动',
      badgeClass: 'amber',
      timingText: '待应用新地址',
      intervalText: intervalText,
      nextText: '应用后重新计算',
      fullTime: '本地草稿已输入新地址',
      guidance: '订阅地址已修改，请先点击【应用修改】使新地址生效'
    };
  }
  const isPendingAirport = Boolean(state?.pending_url_reload?.pending &&
    (!state.pending_url_reload.modified_airports || state.pending_url_reload.modified_airports.includes(sub.name)));
  if (isPendingAirport) {
    return {
      badgeText: '⚠️ 待客户端重载',
      badgeClass: 'amber',
      timingText: sub.updated_at ? formatRelative(sub.updated_at) : '已写入配置',
      intervalText: intervalText,
      nextText: '重载后生效',
      fullTime: sub.updated_at ? formatExact(sub.updated_at) : '新地址已写入配置文件',
      guidance: '新地址已写入配置，请在客户端重新选中卡片并点击【立即更新节点】'
    };
  }
  if (err) {
    return {
      badgeText: '❌ 本次更新失败，仍使用原节点',
      badgeClass: 'red',
      timingText: sub.updated_at ? formatRelative(sub.updated_at) : '时间未知',
      intervalText: intervalText,
      nextText: '请检查网络或稍后重试',
      fullTime: sub.updated_at ? formatExact(sub.updated_at) : '无有效更新记录',
      guidance: `更新失败: ${err}。仍保留原有节点使用。`
    };
  }
  if (sub.nodes === 0) {
    return {
      badgeText: '⚠️ 暂无可用节点',
      badgeClass: 'amber',
      timingText: sub.updated_at ? formatRelative(sub.updated_at) : '时间未知',
      intervalText: intervalText,
      nextText: '可尝试手动更新',
      fullTime: sub.updated_at ? formatExact(sub.updated_at) : '无有效更新记录',
      guidance: '节点列表为空，请检查订阅链接或尝试更新。'
    };
  }
  if (!sub.updated_at || sub.updated_at.startsWith('0001-01-01')) {
    return {
      badgeText: '⚪ 尚未取得更新时间',
      badgeClass: 'muted',
      timingText: '尚未取得更新时间',
      intervalText: intervalText,
      nextText: '将在首次更新后确定',
      fullTime: '内核中暂无该机场的成功更新时间记录',
      guidance: '未获取到更新记录，可尝试【立即更新节点】'
    };
  }

  const updatedMs = new Date(sub.updated_at).getTime();
  const nowMs = Date.now();
  if (isNaN(updatedMs) || updatedMs > nowMs + 300000) {
    return {
      badgeText: '⚪ 尚未取得更新时间',
      badgeClass: 'muted',
      timingText: '时间记录异常',
      intervalText: intervalText,
      nextText: '—',
      fullTime: sub.updated_at,
      guidance: '时间记录异常，可尝试【立即更新节点】'
    };
  }

  const fullTimeStr = formatExact(sub.updated_at);
  const relativeStr = formatRelative(sub.updated_at);

  if (intervalSec === 0) {
    return {
      badgeText: '⚪ 未开启自动更新',
      badgeClass: 'muted',
      timingText: relativeStr,
      intervalText: '手动更新',
      nextText: '未开启自动更新',
      fullTime: fullTimeStr,
      guidance: '当前配置为手动更新，必要时请点击【立即更新节点】'
    };
  }

  const elapsedSec = (nowMs - updatedMs) / 1000;
  const nextMs = updatedMs + intervalSec * 1000;
  const nextEstimateStr = formatNextEstimate(nextMs);
  const graceSec = Math.min(Math.max(intervalSec * 0.1, 1800), 7200);

  if (elapsedSec <= intervalSec) {
    return {
      badgeText: '✅ 最近已更新，通常无需手动更新',
      badgeClass: 'teal',
      timingText: relativeStr,
      intervalText: intervalText,
      nextText: nextEstimateStr,
      fullTime: fullTimeStr,
      guidance: '节点由内核按计划自动更新，当前无需操作'
    };
  }

  if (elapsedSec <= intervalSec + graceSec) {
    return {
      badgeText: '⏳ 已到计划更新时间，等待更新确认',
      badgeClass: 'blue',
      timingText: relativeStr,
      intervalText: intervalText,
      nextText: '等待自动轮询确认',
      fullTime: fullTimeStr,
      guidance: '已达计划更新周期，内核正在等待自动轮询更新'
    };
  }

  return {
    badgeText: '⚠️ 更新略有延迟，可手动更新一次',
    badgeClass: 'amber',
    timingText: relativeStr,
    intervalText: intervalText,
    nextText: '已逾期，建议检查',
    fullTime: fullTimeStr,
    guidance: '已超过预期更新时间，若节点不可用可点击【立即更新节点】'
  };
}

async function api(path, data) {
  const options = { credentials: 'same-origin', headers: { 'X-Clash-Console': '1' } };
  if (data !== undefined) {
    options.method = 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.message || '操作失败');
    error.status = response.status;
    error.code = result.code;
    error.rev = result.rev;
    throw error;
  }
  return result;
}

let toastTimer;
function toast(message) {
  $('toast').textContent = message;
  $('toast').classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.add('hidden'), 5000);
}

function navigate(next) {
  closeSyncModal();
  page = next;
  document.querySelectorAll('.page').forEach((el) => el.classList.toggle('hidden', el.id !== 'page-' + next));
  document.querySelectorAll('.nav').forEach((el) => el.classList.toggle('active', el.dataset.page === next));
  $('breadcrumb').textContent = '本地配置 / ' + names[next];
}

function setBusy(busy) {
  document.body.classList.toggle('busy', busy);
  document.querySelectorAll('button').forEach((button) => {
    if (!button.classList.contains('nav') && !button.dataset.go) button.disabled = busy;
  });
}

function getSyncBadgeInfo() {
  const isPendingUrl = Boolean(state?.pending_url_reload?.pending);
  if (!state?.live?.connected) {
    return { text: 'Clash 未连接', cls: 'badge amber' };
  }
  if (state.draft_changed) {
    return { text: '草稿未保存', cls: 'badge amber' };
  }
  if (!state.core_synced && isPendingUrl) {
    return { text: '文件已同步，待重载', cls: 'badge amber' };
  }
  if (!state.core_synced) {
    return { text: '文件已同步，待生效', cls: 'badge amber' };
  }
  if (isPendingUrl) {
    return { text: '规则已生效，地址待重载', cls: 'badge amber' };
  }
  return { text: '运行中已生效', cls: 'badge teal' };
}

function updateCoreSyncBar() {
  const bar = $('core-sync-bar');
  if (!bar) return;
  const isPendingUrl = Boolean(state?.pending_url_reload?.pending);
  const targetProfile = state?.target_profile || 'ProxyMatrix 统一配置';
  const targetFile = state?.pending_url_reload?.target_file || '';
  const liveRules = state?.core_status?.live_rules_count ?? '160';
  const expectedRules = state?.core_status?.expected_rules_count ?? '164';

  const btnAck = $('btn-ack-core-sync');
  const textEl = $('core-sync-bar-text') || (bar.querySelector ? bar.querySelector('span') : null);

  if (isPendingUrl) {
    bar.classList.remove('hidden');
    if (btnAck) btnAck.classList.remove('hidden');
    if (textEl) {
      if (state?.core_synced) {
        textEl.innerHTML = `⚠️ <b>分流规则已生效，机场订阅地址待重载</b>：分流规则已在内核生效（${esc(liveRules)} 条）。内核无法自动检测新订阅地址，请在 Clash Verge 客户端中点击配置卡片<b>【${esc(targetProfile)}】</b>${targetFile ? `<small style="color:var(--muted);"> (${esc(targetFile)})</small>` : ''}重载，并在机场卡片点击【立即更新节点】。`;
      } else {
        textEl.innerHTML = `⚠️ <b>配置已同步至客户端，待内核重载</b>：最新配置文件已更新（规则预期 <b>${esc(expectedRules)}</b> 条，内核运行 <b>${esc(liveRules)}</b> 条，机场订阅地址已变更）。请在 Clash Verge 客户端中点击配置卡片<b>【${esc(targetProfile)}】</b>${targetFile ? `<small style="color:var(--muted);"> (${esc(targetFile)})</small>` : ''}重载，并在机场卡片点击【立即更新节点】。`;
      }
    }
  } else if (!state?.core_synced && state?.imported && !state?.draft_changed) {
    bar.classList.remove('hidden');
    if (btnAck) btnAck.classList.add('hidden');
    if (textEl) {
      textEl.innerHTML = `⚠️ <b>配置已同步至客户端，待内核重载</b>：最新配置文件已更新（最新生成 <b>${esc(expectedRules)}</b> 条规则，当前内核运行中为 <b>${esc(liveRules)}</b> 条）。请在 Clash Verge 客户端中切换配置卡片或重启内核以重载运行中的策略。`;
    }
  } else {
    bar.classList.add('hidden');
    if (btnAck) btnAck.classList.add('hidden');
  }
}

function updateLiveStatus() {
  if (!state || typeof document === 'undefined') return;
  const badgeInfo = getSyncBadgeInfo();
  if ($('live-badge')) {
    $('live-badge').textContent = badgeInfo.text;
    $('live-badge').className = badgeInfo.cls;
  }
  const live = state.live || {};
  const providers = live.providers || {};
  if ($('node-count')) {
    $('node-count').textContent = live.connected ? Object.values(providers).reduce((sum, p) => sum + p.nodes, 0) : '—';
  }
  const selections = live.selections || {};
  if ($('primary-node')) {
    $('primary-node').textContent = selections['🤖 AI 服务'] || selections['🚀 节点选择'] || '默认主节点';
  }
  updateCoreSyncBar();
  if (document.querySelectorAll) {
    const cards = document.querySelectorAll('.subscription-card') || [];
    cards.forEach((card) => {
      const nameEl = card.querySelector ? card.querySelector('.airport-name h2') : null;
      if (!nameEl) return;
      const subName = nameEl.textContent.trim();
      const sub = state.subscriptions?.find((s) => s.name === subName);
      if (sub) {
        const p = providers[sub.name] || {};
        const nodeStatEl = card.querySelector ? card.querySelector('.node-stat') : null;
        if (nodeStatEl) {
          nodeStatEl.textContent = `${sub.nodes ?? p.nodes ?? '—'} 个节点（${sub.alive ?? p.alive ?? '—'} 可用）`;
        }
        const infoEl = card.querySelector ? card.querySelector('.airport-info') : null;
        if (infoEl) {
          const spans = infoEl.querySelectorAll ? infoEl.querySelectorAll('span') : [];
          if (spans.length >= 2) {
            const nodesVal = esc(sub.nodes ?? p.nodes ?? '—');
            const aliveVal = esc(sub.alive ?? p.alive ?? '—');
            const nodesB = spans[0].querySelector ? spans[0].querySelector('b') : null;
            if (nodesB) {
              nodesB.textContent = nodesVal;
            } else {
              spans[0].innerHTML = `节点 <b>${nodesVal}</b>`;
            }
            const aliveB = spans[1].querySelector ? spans[1].querySelector('b') : null;
            if (aliveB) {
              aliveB.textContent = aliveVal;
            } else {
              spans[1].innerHTML = `可用 <b>${aliveVal}</b>`;
            }
          }
        }
      }
    });
  }
}

function showRemoteConflictNotice() {
  const bar = $('remote-conflict-bar');
  if (bar) bar.classList.remove('hidden');
}

function hideRemoteConflictNotice() {
  const bar = $('remote-conflict-bar');
  if (bar) bar.classList.add('hidden');
}

function render() {
  if (!state) return;
  $('version').textContent = state.version || '—';
  updateLiveStatus();
  $('draft-bar').classList.toggle('hidden', !state.draft_changed);
  if ($('conflict-bar')) $('conflict-bar').classList.toggle('hidden', !state.source_changed);
  if (remoteDraftConflict) {
    showRemoteConflictNotice();
  } else {
    hideRemoteConflictNotice();
  }
  if ($('import-notice')) $('import-notice').classList.toggle('hidden', state.imported);
  $('airport-count').textContent = (state.subscriptions || []).length;
  $('site-count').textContent = (state.sites || []).filter((x) => x.enabled !== false).length;

  if ($('remote-sub-url')) {
    const tokenPart = state.sub_token ? '?token=' + encodeURIComponent(state.sub_token) : '';
    $('remote-sub-url').value = window.location.origin + '/clash.yaml' + tokenPart;
  }

  const live = state.live || {};
  const providers = live.providers || {};
  const selections = live.selections || {};
  $('node-count').textContent = live.connected ? Object.values(providers).reduce((sum, p) => sum + p.nodes, 0) : '—';

  $('primary-node').textContent = selections['🤖 AI 服务'] || selections['🚀 节点选择'] || '默认主节点';
  const tracked = ['🤖 AI 服务', '🌐 谷歌服务', '🚀 节点选择', '⚡️ 自动优选', '🎬 国际流媒体'];
  $('selection-list').innerHTML = tracked.map((name) =>
    `<div class="selection-row"><span>${esc(name)}</span><span>${esc(selections[name] || '尚未读取')}</span></div>`
  ).join('');

  const validSubs = new Set((state.subscriptions || []).map((s) => s.name));
  for (const name in pendingUrlInputs) {
    if (!validSubs.has(name)) {
      delete pendingUrlInputs[name];
    }
  }

  document.querySelectorAll('.url-input').forEach((el) => {
    const name = el.dataset?.name;
    if (name && validSubs.has(name)) {
      if (el.value.trim()) {
        pendingUrlInputs[name] = el.value.trim();
      } else {
        delete pendingUrlInputs[name];
      }
    }
  });

  $('subscription-grid').innerHTML = state.subscriptions.length
    ? state.subscriptions.map((sub) => {
        const p = providers[sub.name] || {};
        const pendingVal = pendingUrlInputs[sub.name] || '';
        const timing = computeSubTiming(sub, Boolean(state.live?.connected));
        const isExpanded = expandedDates.has(sub.name);
        const displayTime = isExpanded ? timing.fullTime : timing.timingText;

        return `<article class="panel subscription-card">
          <div class="airport-head">
            <div class="airport-name">
              <span class="airport-symbol">${esc(sub.name.charAt(0).toUpperCase())}</span>
              <div>
                <h2>${esc(sub.name)}</h2>
                <div class="airport-host">${esc(sub.host || '未配置域名')}</div>
              </div>
            </div>
            <span class="sub-badge ${esc(timing.badgeClass)}">${esc(timing.badgeText)}</span>
          </div>

          <div class="sub-timing-box">
            <div class="sub-timing-row">
              <span>最近更新</span>
              <span class="expandable-date" data-name="${esc(sub.name)}" title="点击切换精确/相对时间">${esc(displayTime)}</span>
            </div>
            <div class="sub-timing-row">
              <span>自动更新</span>
              <span>${esc(timing.intervalText)}</span>
            </div>
            <div class="sub-timing-row">
              <span>预计下次</span>
              <span>${esc(timing.nextText)}</span>
            </div>
            <div class="sub-timing-row" style="margin-top: 2px; padding-top: 4px; border-top: 1px dashed #eef2f4;">
              <span>指引建议</span>
              <span style="color:var(--ink); font-weight: 500;">${esc(timing.guidance)}</span>
            </div>
          </div>

          <div class="airport-info">
            <span>节点 <b>${esc(sub.nodes ?? p.nodes ?? '—')}</b></span>
            <span>可用 <b>${esc(sub.alive ?? p.alive ?? '—')}</b></span>
          </div>

          <label class="url-label">更换订阅链接
            <input type="password" autocomplete="off" class="url-input" data-name="${esc(sub.name)}" value="${esc(pendingVal)}" placeholder="已保存地址；粘贴新 URL 替换">
          </label>
          <div class="airport-actions">
            <div>
              <button class="text-button reveal-url" data-name="${esc(sub.name)}">查看已保存地址</button>
              <button class="text-button danger delete-sub" data-name="${esc(sub.name)}">删除</button>
            </div>
            <div>
              <button class="button secondary refresh-sub" data-name="${esc(sub.name)}" style="margin-right: 6px;">立即更新节点</button>
              <button class="button secondary test-subscription" data-name="${esc(sub.name)}">测试订阅</button>
            </div>
          </div>
          <div class="revealed-url hidden"></div>
        </article>`;
      }).join('')
    : '<div class="empty-state" style="grid-column: 1 / -1;"><strong>暂无机场订阅</strong>请在上方输入机场名称与订阅链接添加。</div>';

  document.querySelectorAll('.url-input').forEach((el) => {
    const name = el.dataset?.name;
    if (name && validSubs.has(name) && pendingUrlInputs[name] !== undefined) {
      el.value = pendingUrlInputs[name];
    }
  });

  const current = $('site-group').value;
  $('site-group').innerHTML = state.groups.map((group) => `<option value="${esc(group)}">${esc(group)}</option>`).join('');
  $('site-group').value = state.groups.includes(current) ? current : (state.groups[0] || '🚀 节点选择');
  $('rules-count').textContent = state.sites.length + ' 条';

  $('site-list').innerHTML = state.sites.length
    ? `<table class="rule-table">
        <thead><tr><th>名称 / 域名</th><th>策略组</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          ${state.sites.map((site) => `
            <tr>
              <td>
                <strong>${esc(site.name || site.domain)}</strong>
                <small><code>${esc(site.domain)}</code> · ${esc({ 'DOMAIN': '精确域名', 'DOMAIN-SUFFIX': '包含子域名', 'DOMAIN-KEYWORD': '关键词' }[site.type || 'DOMAIN-SUFFIX'])}</small>
              </td>
              <td>${esc(site.group)}</td>
              <td><button class="toggle-rule ${site.enabled !== false ? 'on' : ''}" data-id="${esc(site.id)}">${site.enabled !== false ? '启用' : '停用'}</button></td>
              <td>
                <button class="text-button edit-rule" data-id="${esc(site.id)}">编辑</button>
                <button class="text-button danger delete-rule" data-id="${esc(site.id)}">删除</button>
              </td>
            </tr>`).join('')}
        </tbody>
      </table>`
    : '<div class="empty-state"><strong>还没有自定义分流规则</strong>在上方输入域名与选择策略组即可新增。</div>';

  if (state.source_changed) toast('本地来源发生修改，建议重新载入。');
}

async function refresh() {
  editActionEpoch++;
  state = await api('/api/state');
  editBaseRev = state?.draft_rev;
  remoteDraftConflict = false;
  hideRemoteConflictNotice();
  render();
}

async function discardLocalEditsAndReload() {
  for (const k in pendingUrlInputs) delete pendingUrlInputs[k];
  if (typeof document !== 'undefined') {
    document.querySelectorAll('.url-input').forEach((el) => {
      el.value = '';
    });
    if ($('new-sub-name')) $('new-sub-name').value = '';
    if ($('new-sub-url')) $('new-sub-url').value = '';
    if ($('site-form')) $('site-form').reset();
    if ($('edit-id')) $('edit-id').value = '';
    if ($('site-name')) $('site-name').value = '';
    if ($('site-domains')) $('site-domains').value = '';
    if ($('cancel-edit')) $('cancel-edit').classList.add('hidden');
    if ($('site-form-title')) $('site-form-title').textContent = '新增分流规则';
    if ($('test-url')) $('test-url').value = '';
  }
  await refresh();
}

function urlChanges() {
  const result = {};
  const validSubs = new Set((state?.subscriptions || []).map((s) => s.name));
  document.querySelectorAll('.url-input').forEach((el) => {
    const name = el.dataset?.name;
    if (name && validSubs.has(name)) {
      const val = el.value.trim();
      if (val) {
        result[name] = val;
        pendingUrlInputs[name] = val;
      } else {
        delete pendingUrlInputs[name];
      }
    }
  });
  for (const name of validSubs) {
    if (pendingUrlInputs[name]) {
      result[name] = pendingUrlInputs[name];
    }
  }
  return result;
}

class ConflictAbortError extends Error {
  constructor(message = '检测到并发编辑冲突，操作已中止。') {
    super(message);
    this.name = 'ConflictAbortError';
    this.isConflict = true;
    this.code = 'conflict';
  }
}

async function save(sites = state?.sites, force = false, urlChangesObj = null) {
  editActionEpoch++;
  const changes = urlChangesObj !== null ? urlChangesObj : urlChanges();
  try {
    const res = await api('/api/draft', { url_changes: changes, sites, base_rev: editBaseRev || state?.draft_rev, force });
    if (changes && typeof changes === 'object') {
      Object.keys(changes).forEach((name) => {
        delete pendingUrlInputs[name];
      });
      document.querySelectorAll('.url-input').forEach((el) => {
        const name = el.dataset?.name;
        if (name && changes[name]) {
          el.value = '';
        }
      });
    }
    await refresh();
    return res;
  } catch (error) {
    if ((error.message && error.message.includes('版本')) || error.code === 'conflict' || error.status === 409) {
      if (confirm('⚠️ 检测到草稿已被其他页面修改！\n\n为防覆盖他人编辑，本次保存已被拦截。\n\n点击【确定】载入最新草稿；点击【取消】保留当前页面内容以便稍后手动处理。')) {
        await discardLocalEditsAndReload();
      }
      throw new ConflictAbortError();
    }
    toast(error.message);
    throw error;
  }
}

function renderResult(job) {
  $('results').classList.remove('hidden');
  $('result-title').textContent = taskNames[job.type] || '操作结果';
  const result = job.result || {};
  const failed = job.state === 'failed' || result.passed === false || result.transport_ok === false;
  $('result-state').textContent = job.state === 'running' ? '正在运行' : failed ? '需要检查' : '已完成';
  $('result-state').className = 'badge ' + (job.state === 'running' ? 'amber' : failed ? 'red' : 'teal');
  $('result-message').textContent = job.state === 'running' ? '正在执行测试或构建操作，请稍候...' : result.message || '操作完成。';

  let html = '';
  if (result.http_status !== undefined || result.nodes !== undefined || result.rules !== undefined || result.version || result.response) {
    html += '<div class="result-info">';
    if (result.http_status !== undefined) html += `<span class="badge">HTTP ${esc(result.http_status || '未响应')}</span>`;
    if (result.nodes !== undefined) html += `<span class="badge">识别节点数 ${esc(result.nodes)}</span>`;
    if (result.rules !== undefined) html += `<span class="badge">总规则数 ${esc(result.rules)}</span>`;
    if (result.version) html += `<span class="badge">版本 ${esc(result.version)}</span>`;
    if (result.response) html += `<span class="badge teal">模型返回: ${esc(result.response)}</span>`;
    html += '</div>';
  }
  if (result.providers) {
    html += '<div style="margin-top: 12px; display: grid; gap: 8px;">';
    Object.values(result.providers).forEach((p) => {
      const isOk = p.status === 'success';
      const isUnapplied = p.status === 'unapplied';
      const badgeClass = isOk ? 'teal' : isUnapplied ? 'amber' : 'red';
      const badgeText = isOk ? '刷新成功' : isUnapplied ? '待应用配置' : '刷新失败';
      const detail = isOk ? `识别节点 ${p.nodes || 0} 个 · 活跃 ${p.alive || 0} 个` : p.error || '失败';
      html += `
        <div class="result-item" style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <strong>${esc(p.name)}</strong>
            <small style="display:block; color:var(--text-soft);">${esc(detail)}</small>
          </div>
          <span class="badge ${badgeClass}">${badgeText}</span>
        </div>
      `;
    });
    html += '</div>';
  }
  const routes = result.routes || (result.host ? [result] : []);
  html += routes.map((route) => `
    <div class="result-item">
      <strong>${esc(route.host)}</strong>
      <span class="badge ${route.tls_ok === false ? 'red' : 'teal'}">${route.tls_ok === false ? '连接失败' : '连接成功'}</span>
      <div class="route-chain">${esc((route.chains || []).slice().reverse().join(' → ') || '未捕获代理链路')}</div>
      ${route.rule ? `<div class="route-chain">命中规则: ${esc(route.rule)} ${esc(route.rule_payload || '')}</div>` : ''}
    </div>
  `).join('');
  $('result-details').innerHTML = html;
}

async function task(type, extra = {}) {
  if (activeJob) return toast('已有任务正在运行，请稍候。');
  try {
    await save();
  } catch (error) {
    if (error.isConflict) return;
    return toast(error.message);
  }
  try {
    const job = await api('/api/jobs', { type, ...extra });
    activeJob = job.id;
    setBusy(true);
    renderResult({ type, state: 'running' });
    $('results').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    while (activeJob) {
      await new Promise((resolve) => setTimeout(resolve, 900));
      const current = await api('/api/jobs/' + activeJob);
      renderResult(current);
      if (current.state !== 'running') {
        activeJob = null;
        setBusy(false);
        if (current.type === 'refresh_providers' && current.result?.providers) {
          for (const [pname, pdata] of Object.entries(current.result.providers)) {
            if (pdata.status === 'failed') {
              lastRefreshErrors[pname] = pdata.error || '刷新失败';
            } else if (pdata.status === 'success') {
              delete lastRefreshErrors[pname];
            }
          }
        }
        await refresh();
        toast(current.result?.message || '任务执行完成');
      }
    }
  } catch (error) {
    activeJob = null;
    setBusy(false);
    toast(error.message);
  }
}

let currentPublishSession = 0;

function closeSyncModal() {
  pollActive = false;
  currentPublishSession++;
  if (pollTimeoutId) {
    clearTimeout(pollTimeoutId);
    pollTimeoutId = null;
  }
  if ($('sync-modal')) $('sync-modal').classList.add('hidden');
  render();
}

function renderModalSuccess(liveRules) {
  $('sync-modal-title').textContent = '应用成功';
  $('sync-modal-body').innerHTML = `
    <div class="success-box">
      <strong>✅ 配置已成功载入内核运行！</strong>
      <p>当前运行规则：<b>${esc(liveRules)}</b> 条全部生效</p>
      <p style="font-size: 11px; color: var(--muted); margin-top: 6px;">确认生效时间：${formatExact(new Date().toISOString())}</p>
    </div>
  `;
  $('sync-modal-footer').innerHTML = `<button type="button" class="button close-sync-modal" id="btn-close-sync-modal">完成</button>`;
}

function renderModalRuleSyncedWithUrlPending(targetProfile, targetFile, liveRules) {
  $('sync-modal-title').textContent = '分流规则已生效，订阅地址仍待重载';
  $('sync-modal-body').innerHTML = `
    <div class="guidance-box" style="margin-top: 4px;">
      <strong style="color:var(--teal);">✅ 自定义分流规则已载入内核运行（共 ${esc(liveRules)} 条规则）</strong>
      <p style="margin: 8px 0 4px; font-weight: 600; color: #b42318;">⚠️ 注意：检测到本次同时修改了机场订阅地址</p>
      <p style="margin: 0 0 8px;">内核无法对外提供查询生效地址的接口，因此无法自动确认新地址加载。请确保已完成以下步骤：</p>
      <ol>
        <li>在 Clash Verge 客户端中点击配置卡片 <b>【${esc(targetProfile)}】</b>${targetFile ? `<small style="color:var(--muted);"> (${esc(targetFile)})</small>` : ''}。</li>
        <li>回到本页面，在对应机场卡片上点击 <b>【立即更新节点】</b> 以同步新节点。</li>
      </ol>
    </div>
    <div style="font-size: 11px; color: var(--muted); margin-top: 10px; text-align: right;">规则确认时间：${formatExact(new Date().toISOString())}</div>
  `;
  $('sync-modal-footer').innerHTML = `
    <button type="button" class="button secondary close-sync-modal" id="btn-close-sync-modal">稍后处理（保留提醒）</button>
    <button type="button" class="button btn-ack-url" id="btn-ack-url-reload">我已在客户端重载</button>
  `;
}

function scheduleNextPoll(targetProfile, expectedRules, urlsPending, targetFile, session) {
  if (!pollActive || session !== currentPublishSession) return;
  pollTimeoutId = setTimeout(() => checkModalSync(targetProfile, expectedRules, urlsPending, targetFile, session), 2000);
}

async function checkModalSync(targetProfile, expectedRules, urlsPending, targetFile, session) {
  if (!pollActive || session !== currentPublishSession) return;
  try {
    const st = await api('/api/state');
    if (session !== currentPublishSession) return;
    mergeBackgroundState(st);
    if (st.core_synced) {
      pollActive = false;
      const liveCount = st.core_status?.live_rules_count || expectedRules;
      if (urlsPending || Boolean(st.pending_url_reload?.pending)) {
        renderModalRuleSyncedWithUrlPending(targetProfile, targetFile, liveCount);
      } else {
        renderModalSuccess(liveCount);
      }
      render();
      return;
    }
    const pollEl = $('modal-poll-text');
    if (pollEl) {
      const curLive = st.core_status?.live_rules_count ?? '—';
      pollEl.textContent = `正在检测内核状态...（当前 ${curLive} 条，预期 ${expectedRules} 条）`;
    }
  } catch (e) {
    console.warn('Sync check error:', e);
  } finally {
    if (pollActive && session === currentPublishSession) {
      scheduleNextPoll(targetProfile, expectedRules, urlsPending, targetFile, session);
    }
  }
}

function handlePublishResult(result) {
  const session = ++currentPublishSession;
  const targetProfile = result.target_profile || state?.target_profile || 'ProxyMatrix 统一配置';
  const targetFile = result.target_file || '';
  const urlsPending = Boolean(result.urls_modified || result.urls_pending || state?.pending_url_reload?.pending);
  const coreSynced = Boolean(result.core_synced);
  const expectedRules = result.expected_rules_count ?? (state?.core_status?.expected_rules_count || 164);
  const liveRules = result.live_rules_count ?? (state?.core_status?.live_rules_count || 160);

  $('sync-modal').classList.remove('hidden');

  if (urlsPending) {
    if (coreSynced) {
      renderModalRuleSyncedWithUrlPending(targetProfile, targetFile, liveRules);
    } else {
      $('sync-modal-title').textContent = '配置已更新，待客户端重载';
      $('sync-modal-body').innerHTML = `
        <p>已成功生成配置并写入 Clash Verge 配置文件（卡片：<b>【${esc(targetProfile)}】</b>${targetFile ? `<small style="color:var(--muted);"> (${esc(targetFile)})</small>` : ''}）。</p>
        <div class="guidance-box" style="margin-top: 12px;">
          <strong>⚠️ 检测到机场订阅地址修改</strong>
          <p style="margin: 4px 0 8px;">内核无法对外暴露当前使用的订阅地址，因此无法通过接口自动确认新地址加载。请执行以下步骤生效：</p>
          <ol>
            <li>在 Clash Verge 客户端中点击配置卡片 <b>【${esc(targetProfile)}】</b>。</li>
            <li>回到本页面，在对应机场卡片上点击 <b>【立即更新节点】</b>。</li>
          </ol>
        </div>
        <div id="modal-poll-text" style="margin-top: 12px; font-size: 12px; color: var(--muted);">正在后台检测分流规则同步状态（当前 ${liveRules} 条，预期 ${expectedRules} 条）...</div>
      `;
      $('sync-modal-footer').innerHTML = `
        <button type="button" class="button secondary close-sync-modal" id="btn-close-sync-modal">稍后处理（保留提醒）</button>
        <button type="button" class="button btn-ack-url" id="btn-ack-url-reload">我已在客户端重载</button>
      `;
      pollActive = true;
      scheduleNextPoll(targetProfile, expectedRules, urlsPending, targetFile, session);
    }
  } else if (!coreSynced) {
    $('sync-modal-title').textContent = '等待内核载入配置';
    $('sync-modal-body').innerHTML = `
      <p>配置已同步至客户端文件（卡片：<b>【${esc(targetProfile)}】</b>${targetFile ? `<small style="color:var(--muted);"> (${esc(targetFile)})</small>` : ''}），等待内核重载生效：</p>
      <div style="background: #f8fafb; border-radius: 6px; padding: 10px 12px; margin: 10px 0; font-size: 12px;">
        <div>最新配置规则：<b>${esc(expectedRules)}</b> 条</div>
        <div>当前内核运行：<b>${esc(liveRules)}</b> 条</div>
      </div>
      <div class="guidance-box">
        <strong>请在客户端完成重载：</strong>
        <ol>
          <li>打开 Clash Verge 客户端。</li>
          <li>在配置页面重新点击卡片 <b>【${esc(targetProfile)}】</b>。</li>
          <li>本页面将自动检测并显示生效结果。</li>
        </ol>
      </div>
      <div id="modal-poll-text" style="margin-top: 12px; font-size: 12px; color: var(--muted);">正在自动检测生效状态...</div>
    `;
    $('sync-modal-footer').innerHTML = `<button type="button" class="button secondary close-sync-modal" id="btn-close-sync-modal">稍后处理</button>`;
    pollActive = true;
    scheduleNextPoll(targetProfile, expectedRules, false, targetFile, session);
  } else {
    renderModalSuccess(expectedRules);
  }
}

async function applyAll() {
  if (activeJob) return toast('已有任务正在运行，请稍候。');

  // 1. Collect site form uncommitted edits if present
  let sitesToSave = state?.sites ? [...state.sites] : [];
  let formHasEdits = false;
  const rawDomainsInput = $('site-domains');
  const rawDomainsVal = rawDomainsInput ? rawDomainsInput.value.trim() : '';

  if (rawDomainsVal) {
    const rawDomains = rawDomainsVal.split('\n').map((x) => x.trim()).filter(Boolean);
    if (!rawDomains.length) {
      toast('网站分流表单中的域名格式不正确');
      rawDomainsInput.focus();
      return;
    }
    formHasEdits = true;
    const id = $('edit-id') ? $('edit-id').value.trim() : '';
    const name = $('site-name') ? $('site-name').value.trim() : '';
    const group = $('site-group') ? $('site-group').value : '🚀 节点选择';
    const type = $('site-type') ? $('site-type').value : 'DOMAIN-SUFFIX';

    if (id) {
      sitesToSave = sitesToSave.map((s) => (s.id === id ? { ...s, name, group, type, domain: rawDomains[0] } : s));
    } else {
      for (const domain of rawDomains) {
        sitesToSave.push({ id: Math.random().toString(36).slice(2), name, group, type, domain, enabled: true });
      }
    }
  }

  // 2. Collect URL changes
  const changes = urlChanges();

  // 3. Save draft with collected data
  try {
    await save(sitesToSave, false, changes);
    if (formHasEdits && $('site-form')) {
      $('site-form').reset();
      if ($('edit-id')) $('edit-id').value = '';
      if ($('site-form-title')) $('site-form-title').textContent = '新增分流规则';
      if ($('cancel-edit')) $('cancel-edit').classList.add('hidden');
    }
  } catch (error) {
    if (error.isConflict) return;
    return toast(error.message);
  }

  // 4. Run publish job
  try {
    $('sync-modal-title').textContent = '正在应用修改...';
    $('sync-modal-body').innerHTML = `
      <div style="text-align: center; padding: 20px 0;">
        <div class="spinner" style="margin: 0 auto 12px;"></div>
        <div>正在生成配置并写入 Clash Verge...</div>
      </div>
    `;
    $('sync-modal-footer').innerHTML = `<button type="button" class="button secondary close-sync-modal" id="btn-close-sync-modal">取消</button>`;
    $('sync-modal').classList.remove('hidden');

    const job = await api('/api/jobs', { type: 'publish' });
    activeJob = job.id;
    setBusy(true);

    while (activeJob) {
      await new Promise((resolve) => setTimeout(resolve, 800));
      const current = await api('/api/jobs/' + activeJob);
      if (current.state !== 'running') {
        activeJob = null;
        setBusy(false);
        await refresh();
        if (current.state === 'failed' || current.result?.published === false) {
          closeSyncModal();
          toast(current.result?.message || '生成或应用配置失败');
          renderResult(current);
        } else {
          handlePublishResult(current.result || {});
        }
      }
    }
  } catch (error) {
    activeJob = null;
    setBusy(false);
    closeSyncModal();
    toast(error.message);
  }
}

document.addEventListener('click', async (event) => {
  const expandEl = event.target?.closest ? event.target.closest('.expandable-date') : null;
  if (expandEl && expandEl.dataset?.name) {
    const name = expandEl.dataset.name;
    if (expandedDates.has(name)) {
      expandedDates.delete(name);
    } else {
      expandedDates.add(name);
    }
    render();
    return;
  }

  if ($('sync-modal') && event.target === $('sync-modal')) {
    closeSyncModal();
    return;
  }

  const button = event.target?.closest ? event.target.closest('button') : null;
  if (!button) return;
  try {
    if (button.classList?.contains('btn-ack-url') || button.id === 'btn-ack-url-reload' || button.id === 'btn-ack-core-sync') {
      return ackUrlReload();
    }
    if (button.classList?.contains('close-sync-modal') || button.id === 'btn-close-sync-modal' || button.id === 'btn-close-sync-modal-x') {
      closeSyncModal();
      return;
    }
    if (button.id === 'btn-reload-remote-draft') {
      const hasEdits = Boolean(Object.keys(urlChanges()).length || ($('site-domains') && $('site-domains').value.trim()));
      if (hasEdits) {
        if (!confirm('载入最新草稿将丢弃当前窗口未保存的内容，是否继续？')) {
          return;
        }
      }
      await discardLocalEditsAndReload();
      return toast('已载入最新草稿。');
    }
    if (button.classList?.contains('btn-apply-all')) {
      return applyAll();
    }
    if (button.dataset.page) return navigate(button.dataset.page);
    if (button.dataset.go) return navigate(button.dataset.go);
    if (button.dataset.task) return task(button.dataset.task);
    if (button.id === 'save-urls') {
      try {
        await save();
        return toast('订阅修改已暂存为草稿。');
      } catch (error) {
        if (error.isConflict) return;
        return toast(error.message);
      }
    }
    if (button.id === 'btn-add-sub') {
      const name = $('new-sub-name').value.trim();
      const url = $('new-sub-url').value.trim();
      if (!name || !url) return toast('请填写完整的机场名称与订阅链接。');
      try {
        editActionEpoch++;
        await api('/api/draft', {
          url_changes: { [name]: url },
          base_rev: editBaseRev || state?.draft_rev
        });
        $('new-sub-name').value = '';
        $('new-sub-url').value = '';
        await refresh();
        return toast(`已添加机场 [${name}] 到草稿。`);
      } catch (error) {
        if ((error.message && error.message.includes('版本')) || error.code === 'conflict' || error.status === 409) {
          if (confirm('⚠️ 检测到草稿已被其他页面修改！\n\n为防覆盖他人编辑，本次保存已被拦截。\n\n点击【确定】载入最新草稿；点击【取消】保留当前页面内容以便稍后手动处理。')) {
            await discardLocalEditsAndReload();
          }
          return;
        }
        return toast(error.message);
      }
    }
    if (button.classList.contains('delete-sub')) {
      const name = button.dataset.name;
      if (!confirm(`确定要从配置中删除机场 [${name}] 吗？`)) return;
      try {
        editActionEpoch++;
        await api('/api/draft', {
          delete_subscription: name,
          base_rev: editBaseRev || state?.draft_rev
        });
        delete pendingUrlInputs[name];
        const card = button.closest('.subscription-card');
        if (card) {
          const inp = card.querySelector('.url-input');
          if (inp) inp.value = '';
        }
        await refresh();
        return toast(`已删除机场 [${name}]。`);
      } catch (error) {
        if ((error.message && error.message.includes('版本')) || error.code === 'conflict' || error.status === 409) {
          if (confirm('⚠️ 检测到草稿已被其他页面修改！\n\n为防覆盖他人编辑，本次操作已被拦截。\n\n点击【确定】载入最新草稿；点击【取消】保留当前页面。')) {
            await discardLocalEditsAndReload();
          }
          return;
        }
        return toast(error.message);
      }
    }
    if (button.classList.contains('refresh-sub')) {
      return task('refresh_providers', { name: button.dataset.name });
    }
    if (button.classList.contains('test-subscription')) {
      return task('subscription', { name: button.dataset.name });
    }
    if (button.classList.contains('reveal-url')) {
      const panel = button.closest('.subscription-card').querySelector('.revealed-url');
      if (!panel.classList.contains('hidden')) {
        panel.classList.add('hidden');
        button.textContent = '查看已保存地址';
        return;
      }
      const data = await api('/api/url?name=' + encodeURIComponent(button.dataset.name));
      panel.textContent = data.url;
      panel.classList.remove('hidden');
      button.textContent = '隐藏地址';
      return;
    }
    if (button.classList.contains('toggle-rule')) {
      const id = button.dataset.id;
      const sites = state.sites.map((s) => s.id === id ? { ...s, enabled: s.enabled === false } : s);
      try {
        await save(sites);
      } catch (error) {
        if (!error.isConflict) toast(error.message);
      }
      return;
    }
    if (button.classList.contains('delete-rule')) {
      const id = button.dataset.id;
      const sites = state.sites.filter((s) => s.id !== id);
      try {
        await save(sites);
        toast('已从草稿中删除规则。');
      } catch (error) {
        if (!error.isConflict) toast(error.message);
      }
      return;
    }
    if (button.classList.contains('edit-rule')) {
      const item = state.sites.find((s) => s.id === button.dataset.id);
      if (!item) return;
      $('edit-id').value = item.id;
      $('site-name').value = item.name || '';
      $('site-domains').value = item.domain;
      $('site-type').value = item.type || 'DOMAIN-SUFFIX';
      $('site-group').value = item.group;
      $('site-form-title').textContent = '编辑分流规则';
      $('cancel-edit').classList.remove('hidden');
      $('site-form').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      return;
    }
    if (button.id === 'cancel-edit') {
      $('site-form').reset();
      $('edit-id').value = '';
      $('site-form-title').textContent = '新增分流规则';
      $('cancel-edit').classList.add('hidden');
      return;
    }
    if (button.id === 'reset-draft' || button.id === 'btn-reload-draft') {
      if (!confirm('确定放弃所有未保存修改并重新载入磁盘最新配置吗？')) return;
      try {
        await api('/api/draft/reset', {});
        await discardLocalEditsAndReload();
        return toast('已重置草稿并载入磁盘最新配置。');
      } catch (error) {
        return toast(error.message);
      }
    }
  } catch (error) {
    toast(error.message);
  }
});

$('site-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const id = $('edit-id').value.trim();
  const name = $('site-name').value.trim();
  const group = $('site-group').value;
  const type = $('site-type').value;
  const rawDomains = $('site-domains').value.split('\n').map((x) => x.trim()).filter(Boolean);
  if (!rawDomains.length) return toast('请输入至少一个域名或网址。');

  let sites = [...state.sites];
  if (id) {
    sites = sites.map((s) => s.id === id ? { ...s, name, group, type, domain: rawDomains[0] } : s);
  } else {
    for (const domain of rawDomains) {
      sites.push({ id: Math.random().toString(36).slice(2), name, group, type, domain, enabled: true });
    }
  }
  try {
    await save(sites);
    $('site-form').reset();
    $('edit-id').value = '';
    $('site-form-title').textContent = '新增分流规则';
    $('cancel-edit').classList.add('hidden');
    toast('网站规则已保存至草稿。');
  } catch (error) {
    if (error.isConflict) {
      return;
    }
    toast(error.message);
  }
});

$('website-test-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const url = $('test-url').value.trim();
  if (!url) return;
  task('website', { url });
});

if ($('copy-sub-url') && $('remote-sub-url')) {
  $('copy-sub-url').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('remote-sub-url').value);
      toast('已复制订阅地址！请在 Clash Verge 顶部粘贴并点击【导入】。');
    } catch {
      $('remote-sub-url').select();
      document.execCommand('copy');
      toast('已复制订阅地址！请在 Clash Verge 顶部粘贴并点击【导入】。');
    }
  });
}

async function init() {
  const hash = window.location.hash.replace(/^#/, '');
  if (hash) {
    try {
      await api('/api/session', { token: hash });
      history.replaceState(null, '', window.location.pathname);
    } catch (e) {
      console.error(e);
    }
  }
  try {
    await refresh();
  } catch (error) {
    if (error.status === 401 || (error.message && error.message.includes('授权'))) {
      if ($('login-notice')) {
        $('login-notice').innerHTML = `
          <div style="display: flex; flex-direction: column; gap: 8px;">
            <div><b>未授权访问管理台</b>：${esc(error.message)}</div>
            <div style="display: flex; gap: 8px; align-items: center; margin-top: 4px;">
              <input id="login-token-input" type="password" placeholder="输入访问密钥 (Token)" style="padding: 6px 10px; font-size: 13px; border-radius: 4px; border: 1px solid #ccc; flex: 1;">
              <button id="btn-login" class="button" type="button" style="padding: 6px 14px; font-size: 13px;">登录并记住本设备</button>
            </div>
            <small style="color: #666;">提示：也可直接在文件夹中双击 <b>【打开管理台.command】</b> 免密自动登录并记住设备。</small>
          </div>
        `;
        $('login-notice').classList.remove('hidden');
        const loginBtn = $('btn-login');
        if (loginBtn) {
          loginBtn.onclick = async () => {
            const inp = $('login-token-input');
            const tok = (inp && inp.value.trim()) || '';
            if (!tok) return toast('请输入访问密钥');
            try {
              await api('/api/session', { token: tok });
              $('login-notice').classList.add('hidden');
              await refresh();
              toast('登录成功！已在本机浏览器记住登录状态。');
            } catch (e) {
              toast(e.message);
            }
          };
        }
      }
    } else {
      toast('无法获取管理台状态: ' + error.message);
    }
  }
}

function updateCardTimings() {
  if (!state || !state.subscriptions || typeof document === 'undefined' || !document.querySelectorAll) return;
  const isLiveConnected = Boolean(state.live?.connected);
  state.subscriptions.forEach((sub) => {
    const timing = computeSubTiming(sub, isLiveConnected);
    const isExpanded = expandedDates.has(sub.name);
    const displayTime = isExpanded ? timing.fullTime : timing.timingText;

    const cards = document.querySelectorAll('.subscription-card') || [];
    cards.forEach((card) => {
      const nameEl = card.querySelector ? card.querySelector('.airport-name h2') : null;
      if (nameEl && nameEl.textContent.trim() === sub.name) {
        const badgeEl = card.querySelector ? card.querySelector('.sub-badge') : null;
        if (badgeEl) {
          badgeEl.className = 'sub-badge ' + esc(timing.badgeClass);
          badgeEl.textContent = timing.badgeText;
        }
        const dateEl = card.querySelector ? card.querySelector('.expandable-date') : null;
        if (dateEl) {
          dateEl.textContent = displayTime;
        }
        const rows = card.querySelectorAll ? card.querySelectorAll('.sub-timing-row') : [];
        if (rows.length >= 4) {
          const autoVal = rows[1].querySelector ? rows[1].querySelector('span:last-child') : null;
          if (autoVal) autoVal.textContent = timing.intervalText;
          const nextVal = rows[2].querySelector ? rows[2].querySelector('span:last-child') : null;
          if (nextVal) nextVal.textContent = timing.nextText;
          const guideVal = rows[3].querySelector ? rows[3].querySelector('span:last-child') : null;
          if (guideVal) guideVal.textContent = timing.guidance;
        }
      }
    });
  });
}

async function ackUrlReload() {
  try {
    await api('/api/url_reload/ack', {});
    if (state?.pending_url_reload) {
      state.pending_url_reload = { pending: false };
    }
    closeSyncModal();
    render();
    toast('已确认订阅地址重载！');
  } catch (e) {
    toast(e.message);
  }
}

function mergeBackgroundState(st) {
  if (!st || !state) return;

  // 1. Check for remote draft conflicts without advancing editor base_rev!
  if (st.draft_rev && editBaseRev && st.draft_rev !== editBaseRev) {
    remoteDraftConflict = true;
    showRemoteConflictNotice();
  }

  // 2. NEVER advance editBaseRev or state.draft_rev!
  // NEVER overwrite state.sites or user-edited subscription URLs!

  // 3. Merge ONLY runtime and live kernel information:
  state.live = st.live || { connected: false };
  state.core_synced = st.core_synced;
  state.core_status = st.core_status;
  state.target_profile = st.target_profile || state.target_profile;
  state.pending_url_reload = st.pending_url_reload;

  if (Array.isArray(st.subscriptions) && Array.isArray(state.subscriptions)) {
    const liveSubMap = new Map(st.subscriptions.map((s) => [s.name, s]));
    state.subscriptions.forEach((sub) => {
      const liveSub = liveSubMap.get(sub.name);
      if (liveSub) {
        sub.updated_at = liveSub.updated_at;
        sub.nodes = liveSub.nodes;
        sub.alive = liveSub.alive;
        sub.in_kernel = liveSub.in_kernel;
        sub.interval = liveSub.interval;
      }
    });
  }

  // 4. Update UI in-place (DOM textContent/badges only, never touch inputs or forms)
  updateCardTimings();
  updateLiveStatus();
}

async function performBackgroundPoll() {
  if (typeof document === 'undefined' || document.hidden || document.body?.classList?.contains('busy') || pollActive || backgroundPollInFlight) return;
  const pollEpoch = editActionEpoch;
  backgroundPollInFlight = true;
  try {
    const st = await api('/api/state');
    if (pollEpoch !== editActionEpoch) {
      // Race condition protection: an edit/save/refresh occurred while poll was in-flight!
      return;
    }
    mergeBackgroundState(st);
  } catch {
    // Silent background poll
  } finally {
    backgroundPollInFlight = false;
  }
}

if (typeof setInterval !== 'undefined') {
  setInterval(updateCardTimings, 15000);
  setInterval(performBackgroundPoll, 30000);
}

if (typeof globalThis !== 'undefined') {
  globalThis.mergeBackgroundState = mergeBackgroundState;
  globalThis.performBackgroundPoll = performBackgroundPoll;
  globalThis.getEditBaseRev = () => editBaseRev;
  globalThis.getSyncBadgeInfo = getSyncBadgeInfo;
  globalThis.ackUrlReload = ackUrlReload;
  globalThis.updateLiveStatus = updateLiveStatus;
  globalThis.discardLocalEditsAndReload = discardLocalEditsAndReload;
  globalThis.getPendingUrlInputs = () => ({ ...pendingUrlInputs });
  globalThis.getState = () => state;
  globalThis.setState = (s) => { state = s; };
}

init();
