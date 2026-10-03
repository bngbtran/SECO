/*
 * SECO Mobile - member and technician app.
 *
 * Member (USER): the app tracks, it does not charge. Charging happens at the
 * station (plug in, tap the RFID card, pick a budget on the station screen);
 * the app shows the stations and their state, follows the member's own session
 * live, the wallet and history, and lets the member lock / report a lost card,
 * report a broken station, keep favourite stations and read notifications.
 * Technician: member help requests (accept -> resolve), stations needing a visit
 * (live state, open errors, telemetry), maintenance history. Entering / leaving
 * maintenance happens at the station itself (technician card + STOP held 3 s).
 * Admins use the web admin.
 *
 * Every screen reads the deployed API; realtime comes from the /ws WebSocket,
 * with polling as a fallback.
 */

const cfg = window.SECO_CONFIG;
const appRoot = document.querySelector('#app');

// ---------------------------------------------------------------------------
// Formatting & labels
// ---------------------------------------------------------------------------

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (value) => `${Math.round(Number(value) || 0).toLocaleString('vi-VN')}đ`;
/** +50.000đ for a top-up, -125đ for a charge, 0đ when nothing moved. */
const txAmount = (t) => (t.amount_vnd ? `${t.type === 'TOPUP' ? '+' : '-'}${money(t.amount_vnd)}` : money(0));
const kwh = (value, digits = 4) => `${(Number(value) || 0).toFixed(digits)} kWh`;
const pad2 = (n) => String(n).padStart(2, '0');

function fDateTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function fAgo(value) {
  if (!value) return '—';
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return 'vừa xong';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} phút trước`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} giờ trước`;
  return fDateTime(value);
}

function fDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h} giờ ${m} phút`;
  if (m) return `${m} phút ${pad2(s % 60)} giây`;
  return `${s} giây`;
}

const initials = (name) => String(name || '?').trim().split(/\s+/).slice(-2).map((w) => w[0]).join('').toUpperCase();

const AVAILABILITY = {
  AVAILABLE: ['Sẵn sàng', 'green'],
  CHARGING: ['Đang sạc', 'blue'],
  WAITING_PAYMENT: ['Chờ thanh toán', 'blue'],
  ERROR: ['Lỗi', 'red'],
  MAINTENANCE: ['Bảo trì', 'orange'],
  OFFLINE: ['Offline', 'gray']
};
const MAP_COLORS = { green: '#3f796e', blue: '#256fc1', red: '#d94e4d', orange: '#bf7b23', gray: '#8a9793' };

const STOP_REASONS = {
  UNPLUG: 'Rút sạc',
  MAX_BUDGET: 'Đạt ngân sách',
  INSUFFICIENT_BALANCE: 'Hết số dư',
  TECHNICIAN: 'Kỹ thuật viên dừng',
  OVER_TEMPERATURE: 'Trạm quá nhiệt',
  E_STOP: 'Dừng khẩn cấp',
  SYSTEM_ERROR: 'Trạm gặp lỗi'
};

const TICKET_CATEGORIES = {
  CANNOT_START: 'Không bắt đầu sạc được',
  CHARGING_INTERRUPTED: 'Phiên sạc bị gián đoạn',
  CONNECTOR_STUCK: 'Không rút được đầu sạc',
  PAYMENT: 'Thanh toán',
  CARD: 'Thẻ RFID',
  STATION_DAMAGED: 'Trạm bị hư hỏng',
  OTHER: 'Khác'
};
const TICKET_STATUS = {
  OPEN: ['Đang chờ', 'orange'],
  IN_PROGRESS: ['Đang xử lý', 'blue'],
  RESOLVED: ['Đã xử lý', 'green'],
  CANCELLED: ['Đã huỷ', 'gray']
};

const CARD_STATUS = {
  ACTIVE: ['Đang hoạt động', 'green'],
  LOCKED: ['Đã khoá', 'orange'],
  LOST: ['Đã báo mất', 'red'],
  INACTIVE: ['Bị vô hiệu', 'gray'],
  PENDING: ['Chờ quét UID', 'gray']
};

const LOCK_LABEL = { LOCKED: 'Đang khoá', UNLOCKED: 'Mở', UNKNOWN: 'Không rõ' };
const SEVERITY = { WARNING: ['Cảnh báo', 'orange'], ERROR: ['Lỗi', 'red'], CRITICAL: ['Nghiêm trọng', 'red'] };
const ERROR_TYPES = {
  OVER_TEMPERATURE: 'Quá nhiệt', E_STOP: 'Dừng khẩn cấp', SYSTEM_ERROR: 'Lỗi hệ thống',
  SENSOR_ERROR: 'Lỗi cảm biến', COMMUNICATION_ERROR: 'Mất kết nối'
};

const NOTIFICATION_ICONS = {
  CHARGING_COMPLETED: 'bolt', LOW_BALANCE: 'wallet', TOPUP_SUCCEEDED: 'wallet',
  CARD_ASSIGNED: 'card', CARD_BLOCKED: 'lock', CARD_UNLOCKED: 'card',
  SUPPORT_TICKET_ACCEPTED: 'wrench', SUPPORT_TICKET_RESOLVED: 'check', SUPPORT_TICKET_CREATED: 'help',
  STATION_ERROR: 'alert', PASSWORD_CHANGED: 'key', TEMP_PASSWORD_ISSUED: 'key'
};

// API errors (English) -> what the user reads.
const ERRORS_VI = {
  'Invalid credentials': 'Email hoặc mật khẩu không đúng.',
  'User is inactive': 'Tài khoản đã bị khoá. Liên hệ quản trị viên.',
  'Station is not READY': 'Trạm chưa sẵn sàng, hãy chọn trạm khác.',
  'User already has an open session': 'Bạn đang có một phiên sạc chưa kết thúc.',
  'An open charging session already exists': 'Bạn đang có một phiên sạc chưa kết thúc.',
  'Insufficient balance': 'Số dư không đủ.',
  'Insufficient available balance': 'Số dư khả dụng không đủ.',
  'Demo top-up is disabled': 'Nạp tiền demo đang tắt trên máy chủ.',
  'No active charging session': 'Không có phiên sạc đang chạy.',
  'Current password is incorrect': 'Mật khẩu hiện tại không đúng.',
  'New password must be different from the current one': 'Mật khẩu mới phải khác mật khẩu hiện tại.',
  'New password must be different from the temporary one': 'Mật khẩu mới phải khác mật khẩu tạm.',
  'Invalid or expired temporary password': 'Mật khẩu tạm không đúng hoặc đã hết hạn.',
  'Account not found': 'Không tìm thấy tài khoản với email này.',
  'Admin passwords are reset by another admin': 'Tài khoản quản trị: nhờ một quản trị viên khác cấp mật khẩu tạm.',
  'You already have an open support request for this station': 'Bạn đã có một yêu cầu đang mở cho trạm này.',
  'Only an open request that no technician has taken can be cancelled': 'Kỹ thuật viên đã nhận yêu cầu, không thể huỷ.',
  'Request was already taken by another technician or closed': 'Yêu cầu đã được kỹ thuật viên khác nhận hoặc đã đóng.',
  'Accept the request before resolving it': 'Hãy nhận yêu cầu trước khi hoàn tất.',
  'Card was reported lost': 'Thẻ đã được báo mất.',
  'Card was locked by an admin; contact the desk': 'Thẻ do quầy khoá, hãy liên hệ quầy để mở.',
  'Card was disabled by an admin': 'Thẻ đã bị quầy vô hiệu hoá.',
  'A card reported lost cannot be unlocked; ask the desk for a new card': 'Thẻ đã báo mất không mở lại được, hãy liên hệ quầy để cấp thẻ mới.',
  'Station not found': 'Không tìm thấy trạm.',
  'Station is still in ERROR; put it in maintenance or back to READY at the station first': 'Trạm vẫn đang lỗi: vào bảo trì hoặc đưa trạm về sẵn sàng tại trạm trước khi hoàn tất.',
  'Station still has an open session': 'Trạm còn phiên sạc chưa kết thúc, chưa đưa về sẵn sàng được.'
};

const icons = {
  bolt: 'zap', map: 'map', history: 'history', wallet: 'wallet', user: 'user-round', search: 'search', back: 'chevron-left',
  lock: 'lock', unlock: 'lock-open', bell: 'bell', wrench: 'wrench', check: 'check', plug: 'plug', card: 'credit-card',
  plus: 'plus', arrow: 'chevron-right', alert: 'triangle-alert', calendar: 'calendar-days', signal: 'signal', wifi: 'wifi',
  battery: 'battery-full', close: 'x', pin: 'map-pin', navigation: 'navigation', key: 'key-round', help: 'circle-help',
  arrowDown: 'arrow-down-left', list: 'list', star: 'star', phone: 'phone', thermo: 'thermometer', clock: 'clock-3',
  send: 'send', edit: 'pencil', logout: 'log-out', shield: 'shield-alert', refresh: 'refresh-cw', copy: 'copy', inbox: 'inbox'
};
const icon = (name) => `<i class="icon" data-lucide="${icons[name] || name}" aria-hidden="true"></i>`;
const badge = (label, tone = '') => `<span class="badge ${tone}">${esc(label)}</span>`;
const availabilityBadge = (a) => badge(...(AVAILABILITY[a] || [a, 'gray']));

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

const tokenStore = {
  get: () => { try { return localStorage.getItem(cfg.authStorageKey); } catch { return null; } },
  set: (token) => { try { token ? localStorage.setItem(cfg.authStorageKey, token) : localStorage.removeItem(cfg.authStorageKey); } catch { /* private mode */ } }
};

function errorText(body, status) {
  const detail = body?.detail;
  if (typeof detail === 'string') return ERRORS_VI[detail] || detail;
  if (Array.isArray(detail)) return 'Dữ liệu không hợp lệ, hãy kiểm tra lại.';
  return status === 502 ? 'Không kết nối được máy chủ.' : `Lỗi ${status}`;
}

async function api(path, { method = 'GET', body, query } = {}) {
  const url = new URL(cfg.apiBaseUrl.replace(/\/+$/, '') + path, window.location.origin);
  Object.entries(query || {}).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value); });
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), cfg.requestTimeoutMs);
  const token = tokenStore.get();
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {})
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal
    });
  } catch {
    throw new ApiError(0, 'Không kết nối được máy chủ. Máy chủ có thể đang khởi động, thử lại sau ít phút.');
  } finally {
    window.clearTimeout(timeout);
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && token) signOut('Phiên đăng nhập đã hết, hãy đăng nhập lại.');
    throw new ApiError(response.status, errorText(data, response.status));
  }
  return data;
}

/** null when the API answers 404 (e.g. no current session). */
async function apiOrNull(path) {
  try { return await api(path); } catch (e) { if (e.status === 404) return null; throw e; }
}

// ---------------------------------------------------------------------------
// State & navigation
// ---------------------------------------------------------------------------

const state = {
  user: null,
  screen: 'login',
  params: {},
  stack: [],
  data: {},            // per-screen data loaded by the screen's load()
  loading: false,
  stations: [],
  favorites: new Set(),
  wallet: null,
  session: null,       // the member's ACTIVE session
  unread: 0,
  mapFilter: 'ALL',
  query: '',
  mapSelected: null,
  userLocation: null,
  ws: null,
  wsRetry: 0
};

const isMember = () => state.user?.role === 'USER';
const isTechnician = () => state.user?.role === 'TECHNICIAN';
const homeScreen = () => (isTechnician() ? 'techRequests' : 'map');
const stationById = (id) => state.stations.find((s) => s.station_id === id);

function go(screen, params = {}, { replace = false, reset = false } = {}) {
  if (reset) state.stack = [];
  else if (!replace && state.screen !== 'login') state.stack.push({ screen: state.screen, params: state.params });
  state.screen = screen;
  state.params = params;
  state.data = {};
  render();
  void loadScreen();
}

function back() {
  const previous = state.stack.pop();
  if (!previous) return go(homeScreen(), {}, { reset: true });
  state.screen = previous.screen;
  state.params = previous.params;
  state.data = {};
  render();
  void loadScreen();
}

async function loadScreen() {
  const screen = SCREENS[state.screen];
  if (!screen?.load) return;
  const current = state.screen;
  state.loading = true;
  try {
    await screen.load(state.params);
  } catch (e) {
    state.data.error = e.message;
  } finally {
    state.loading = false;
    if (state.screen === current) render();
  }
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

function clock() {
  const d = new Date();
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function shell(body, nav = '') {
  return `<div class="phone"><div class="app-screen"><div class="statusbar"><span>${clock()}</span><span class="status-icons">${icon('signal')}${icon('wifi')}${icon('battery')}</span></div>${body}${nav}</div></div>`;
}

const content = (body, cls = '') => `<section class="content ${cls}">${body}</section>`;

function bell() {
  return `<button class="icon-btn bell" data-go="notifications" aria-label="Thông báo">${icon('bell')}<span class="bell-count" ${state.unread ? '' : 'hidden'}>${state.unread > 9 ? '9+' : state.unread}</span></button>`;
}

function pageHead(title, action = '') {
  return `<div class="topbar"><button class="icon-btn" data-action="back" aria-label="Quay lại">${icon('back')}</button><h1 class="screen-title">${esc(title)}</h1>${action}</div>`;
}

function tabHead(title, subtitle = '', action = '') {
  return `<div class="row tab-head"><div style="min-width:0">${subtitle ? `<div class="eyebrow">${esc(subtitle)}</div>` : ''}<h1 class="screen-title">${esc(title)}</h1></div><div class="row" style="gap:8px">${action}${bell()}</div></div>`;
}

function bottomNav(active) {
  const items = isTechnician()
    ? [['techRequests', 'inbox', 'Yêu cầu'], ['techStations', 'map', 'Trạm'], ['notifications', 'bell', 'Thông báo'], ['account', 'user', 'Tôi']]
    : [['map', 'map', 'Trạm'], ['charge', 'bolt', 'Phiên sạc'], ['wallet', 'wallet', 'Ví'], ['account', 'user', 'Tôi']];
  return `<nav class="bottom-nav">${items.map(([screen, glyph, label]) => `<button class="nav-item ${active === screen ? 'active' : ''}" data-tab="${screen}"><i>${icon(glyph)}</i><span>${label}</span>${screen === 'charge' && state.session ? '<b class="nav-dot"></b>' : ''}${screen === 'notifications' && state.unread ? '<b class="nav-dot"></b>' : ''}</button>`).join('')}</nav>`;
}

const loadingBlock = () => '<div class="loading"><span></span><span></span><span></span></div>';
const errorBlock = (message) => `<div class="notice error">${icon('alert')} ${esc(message)} <button class="btn btn-quiet btn-compact" data-action="reload">Thử lại</button></div>`;
const emptyBlock = (glyph, title, text = '') => `<div class="empty-state"><div class="result-icon">${icon(glyph)}</div><h2>${esc(title)}</h2>${text ? `<p>${text}</p>` : ''}</div>`;

/** Shows the loading / error state until the screen's data has arrived. */
function whenLoaded(key, view) {
  if (state.data.error) return errorBlock(state.data.error);
  if (state.data[key] === undefined) return loadingBlock();
  return view(state.data[key]);
}

function keyval(label, value) {
  return `<div class="keyval"><span>${esc(label)}</span><strong>${value}</strong></div>`;
}

// ---------------------------------------------------------------------------
// Auth screens (every role)
// ---------------------------------------------------------------------------

function loginScreen() {
  return shell(`<section class="login"><div class="logo-mark">${icon('bolt')}</div><h1>Chào mừng trở lại</h1><p class="login-copy">Đăng nhập để tìm trạm, theo dõi phiên sạc và ví của bạn.</p><form class="stack" data-form="login"><div class="field"><label for="email">Email</label><input id="email" name="email" type="email" autocomplete="username" required /></div><div class="field"><label for="password">Mật khẩu</label><input id="password" name="password" type="password" autocomplete="current-password" required /></div><button class="btn btn-primary btn-block">Đăng nhập</button></form><button class="link-btn" data-go="forgotPassword" style="align-self:flex-end;margin-top:10px">Quên mật khẩu?</button><p class="login-foot">Chưa có tài khoản? <strong>Đăng ký tại quầy SECO để được tạo tài khoản và cấp thẻ RFID.</strong><br/>Quản trị viên dùng web admin.</p></section>`);
}

function forgotPasswordScreen() {
  const issued = state.data.issued;
  if (issued) {
    return shell(content(`${pageHead('Quên mật khẩu')}<div class="auth-illustration">${icon('key')}</div><div class="result-head"><h2>Mật khẩu tạm của bạn</h2><p>Dùng mật khẩu này như mật khẩu cũ để đặt mật khẩu mới.</p></div><div class="temp-password"><strong>${esc(issued.temporary_password)}</strong><button class="icon-btn" data-action="copyTemp" aria-label="Sao chép">${icon('copy')}</button></div><p class="subtle" style="text-align:center">Hết hạn lúc ${fDateTime(issued.expires_at)} · chỉ dùng được một lần.<br/>Mật khẩu hiện tại vẫn dùng được cho tới khi bạn đặt mật khẩu mới.</p><div class="sticky-action"><button class="btn btn-primary btn-block" data-go="resetPassword" data-email="${esc(issued.email)}" data-temp="${esc(issued.temporary_password)}">Đặt mật khẩu mới</button></div>`));
  }
  return shell(content(`${pageHead('Quên mật khẩu')}<div class="auth-illustration">${icon('key')}</div><div class="result-head"><h2>Lấy mật khẩu tạm</h2><p>Nhập email tài khoản. Hệ thống tạo một mật khẩu tạm (10 phút) để bạn đặt mật khẩu mới.</p></div><form class="stack" data-form="forgotPassword"><div class="field"><label for="forgot-email">Email tài khoản</label><input id="forgot-email" name="email" type="email" required /></div><button class="btn btn-primary btn-block">Lấy mật khẩu tạm</button></form><div class="notice" style="margin-top:14px">Tài khoản quản trị không dùng cách này: nhờ một quản trị viên khác cấp mật khẩu tạm.</div>`));
}

function resetPasswordScreen({ email = '', temp = '' }) {
  return shell(content(`${pageHead('Đặt mật khẩu mới')}<p class="subtle" style="margin:0 0 16px">Giống đổi mật khẩu: mật khẩu tạm đóng vai trò mật khẩu cũ.</p><form class="stack" data-form="resetPassword"><div class="field"><label>Email</label><input name="email" type="email" value="${esc(email)}" required /></div><div class="field"><label>Mật khẩu tạm</label><input name="temporary_password" value="${esc(temp)}" class="mono" required /></div><div class="field"><label>Mật khẩu mới</label><input name="new_password" type="password" minlength="6" autocomplete="new-password" required /></div><div class="field"><label>Nhập lại mật khẩu mới</label><input name="confirm" type="password" minlength="6" autocomplete="new-password" required /></div><div class="password-rules"><span>✓</span> Tối thiểu 6 ký tự<br/><span>✓</span> Khác mật khẩu tạm</div><button class="btn btn-primary btn-block">Đặt mật khẩu và đăng nhập</button></form>`));
}

function changePasswordScreen() {
  return shell(content(`${pageHead('Đổi mật khẩu')}<p class="subtle" style="margin:0 0 16px">Sau khi đổi, các thiết bị khác sẽ bị đăng xuất.</p><form class="stack" data-form="changePassword"><div class="field"><label>Mật khẩu hiện tại</label><input name="current_password" type="password" autocomplete="current-password" required /></div><div class="field"><label>Mật khẩu mới</label><input name="new_password" type="password" minlength="6" autocomplete="new-password" required /></div><div class="field"><label>Nhập lại mật khẩu mới</label><input name="confirm" type="password" minlength="6" autocomplete="new-password" required /></div><div class="password-rules"><span>✓</span> Tối thiểu 6 ký tự<br/><span>✓</span> Khác mật khẩu hiện tại</div><button class="btn btn-primary btn-block">Cập nhật mật khẩu</button></form>`));
}

function editProfileScreen() {
  const u = state.user;
  return shell(content(`${pageHead('Thông tin cá nhân')}<form class="stack" data-form="editProfile"><div class="field"><label>Họ tên</label><input name="name" value="${esc(u.name)}" maxlength="120" required /></div><div class="field"><label>Số điện thoại</label><input name="phone" value="${esc(u.phone || '')}" inputmode="tel" placeholder="0905 123 456" /></div><div class="field"><label>Email (tên đăng nhập)</label><input value="${esc(u.email)}" disabled /></div><button class="btn btn-primary btn-block">Lưu thay đổi</button></form><div class="notice" style="margin-top:14px">Email là tên đăng nhập nên không đổi được trong ứng dụng. Liên hệ quầy nếu cần đổi email.</div>`));
}

// ---------------------------------------------------------------------------
// Member: stations
// ---------------------------------------------------------------------------

function stationMatches(station) {
  const q = state.query.trim().toLocaleLowerCase('vi-VN');
  const filter = state.mapFilter;
  if (filter === 'AVAILABLE' && station.availability !== 'AVAILABLE') return false;
  if (filter === 'FAVORITE' && !state.favorites.has(station.station_id)) return false;
  if (!q) return true;
  return [station.station_id, station.name, station.address].some((v) => String(v || '').toLocaleLowerCase('vi-VN').includes(q));
}

function filterChips() {
  const counts = {
    ALL: state.stations.length,
    AVAILABLE: state.stations.filter((s) => s.availability === 'AVAILABLE').length,
    FAVORITE: state.stations.filter((s) => state.favorites.has(s.station_id)).length
  };
  return [['ALL', 'Tất cả'], ['AVAILABLE', 'Sẵn sàng'], ['FAVORITE', 'Yêu thích']]
    .map(([key, label]) => `<button class="chip ${state.mapFilter === key ? 'active' : ''}" data-action="mapFilter" data-filter="${key}">${key === 'AVAILABLE' ? '<span class="dot"></span>' : key === 'FAVORITE' ? icon('star') : ''}${label} ${counts[key]}</button>`).join('');
}

function stationSheet(station) {
  if (!station) return '';
  const fav = state.favorites.has(station.station_id);
  return `<div class="station-sheet"><div class="grabber"></div><div class="row" style="align-items:flex-start"><div style="min-width:0"><strong>${esc(station.name)}</strong><div class="subtle">${esc(station.station_id)} · ${esc(station.address || 'Chưa có địa chỉ')}</div></div>${availabilityBadge(station.availability)}</div><div class="metric-grid"><div class="metric"><span>Giá / kWh</span><strong>${money(station.price_per_kwh_vnd)}</strong></div><div class="metric"><span>Khoảng cách</span><strong>${distanceText(station)}</strong></div><div class="metric"><span>Yêu thích</span><strong>${fav ? 'Có' : 'Không'}</strong></div></div><div class="sheet-actions"><button class="btn btn-secondary btn-compact" data-action="directions" data-station="${esc(station.station_id)}">${icon('navigation')} Chỉ đường</button><button class="btn btn-primary btn-compact" data-go="stationDetail" data-station="${esc(station.station_id)}">Xem chi tiết trạm</button></div></div>`;
}

function distanceText(station) {
  if (!state.userLocation || !Number.isFinite(station.latitude)) return '—';
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(station.latitude - state.userLocation.lat);
  const dLng = toRad(station.longitude - state.userLocation.lng);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(state.userLocation.lat)) * Math.cos(toRad(station.latitude)) * Math.sin(dLng / 2) ** 2;
  const km = 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}

function sessionBanner() {
  if (state.session) return `<button class="session-banner" data-tab="charge">${icon('bolt')}<span><strong>Đang sạc tại ${esc(state.session.station_id)}</strong><small>${money(state.session.cost_vnd)} · ${kwh(state.session.energy_kwh)}</small></span>${icon('arrow')}</button>`;
  return '';
}

function mapScreen() {
  const selected = state.mapSelected ? stationById(state.mapSelected) : null;
  return shell(content(`<div class="map-wrap ${selected ? 'has-selection' : 'full-map'}"><div class="map-area"><div id="station-map" class="map-canvas" aria-label="Bản đồ trạm sạc"></div><div class="map-overlay"><div class="row" style="gap:8px"><div class="search map-search" style="flex:1"><span>${icon('search')}</span><input type="search" value="${esc(state.query)}" data-input="query" placeholder="Tìm trạm, địa chỉ..." /></div>${bell()}</div><div class="filters">${filterChips()}<button class="chip" data-go="stations"><span class="chip-icon">${icon('list')}</span>Danh sách</button></div>${sessionBanner()}</div></div><div id="station-sheet">${stationSheet(selected)}</div></div>`, 'no-pad'), bottomNav('map'));
}

function stationsScreen() {
  const list = state.stations.filter(stationMatches);
  return shell(content(`${pageHead('Danh sách trạm')}<div class="search"><span>${icon('search')}</span><input type="search" value="${esc(state.query)}" data-input="query" placeholder="Tìm trạm, địa chỉ..." /></div><div class="filters">${filterChips()}</div><div class="station-list">${list.length ? list.map(stationRow).join('') : emptyBlock('search', 'Không có trạm phù hợp')}</div>`), bottomNav('map'));
}

function stationRow(s) {
  return `<button class="station-row" data-go="stationDetail" data-station="${esc(s.station_id)}"><div class="station-symbol">${icon('bolt')}</div><div class="station-text"><h3>${state.favorites.has(s.station_id) ? `<span class="fav-mark">${icon('star')}</span>` : ''}${esc(s.name)}</h3><p>${esc(s.station_id)} · ${esc(s.address || '')} ${state.userLocation ? `· ${distanceText(s)}` : ''}</p></div>${availabilityBadge(s.availability)}</button>`;
}

function stationDetailScreen({ station: id }) {
  const s = stationById(id);
  if (!s) return shell(content(`${pageHead('Trạm sạc')}${state.stations.length ? emptyBlock('alert', 'Không tìm thấy trạm') : loadingBlock()}`), bottomNav('map'));
  const fav = state.favorites.has(s.station_id);
  const mine = state.session?.station_id === s.station_id;
  const statusText = {
    AVAILABLE: 'Trạm đang trống, có thể tới sạc.',
    CHARGING: 'Trạm đang có xe sạc.',
    WAITING_PAYMENT: 'Trạm đang chờ khách thanh toán.',
    ERROR: 'Trạm đang lỗi, kỹ thuật viên đã được báo.',
    MAINTENANCE: 'Trạm đang bảo trì, tạm thời không sạc được.',
    OFFLINE: 'Trạm mất kết nối, trạng thái có thể chưa cập nhật.'
  }[s.availability] || '';
  return shell(content(`${pageHead(s.name, `<button class="icon-btn ${fav ? 'fav-on' : ''}" data-action="toggleFavorite" data-station="${esc(s.station_id)}" aria-label="Yêu thích">${icon('star')}</button>`)}<div class="station-hero">${availabilityBadge(s.availability)}<h2>${esc(s.station_id)}</h2><p>${icon('pin')} ${esc(s.address || 'Chưa có địa chỉ')}</p><div class="metric-grid"><div class="metric"><span>Giá / kWh</span><strong>${money(s.price_per_kwh_vnd)}</strong></div><div class="metric"><span>Khoảng cách</span><strong>${distanceText(s)}</strong></div><div class="metric"><span>Đầu sạc</span><strong>${LOCK_LABEL[s.connector_lock] || '—'}</strong></div></div></div>${mine ? `<button class="session-banner" data-tab="charge" style="margin-top:10px">${icon('bolt')}<span><strong>Bạn đang sạc tại trạm này</strong><small>Xem phiên sạc</small></span>${icon('arrow')}</button>` : ''}<div class="section-label">Trạng thái</div><div class="info-card row"><div class="station-symbol">${icon(s.availability === 'ERROR' ? 'alert' : 'bolt')}</div><div style="flex:1"><strong>${esc((AVAILABILITY[s.availability] || [s.availability])[0])}</strong><div class="subtle">${statusText}</div></div><small class="subtle">${fAgo(s.last_heartbeat_at)}</small></div><div class="section-label">Cách sạc tại trạm</div><div class="info-card"><div class="step-list"><div class="step"><div class="step-num">1</div><div><strong>Cắm sạc vào xe, quẹt thẻ RFID</strong><span>Màn hình trạm hiện tên và số dư của bạn.</span></div></div><div class="step"><div class="step-num">2</div><div><strong>Chọn ngân sách trên trạm (tuỳ chọn)</strong><span>Gạt joystick để chọn; để yên thì trạm tự sạc sau vài giây.</span></div></div><div class="step"><div class="step-num">3</div><div><strong>Theo dõi trên ứng dụng</strong><span>Rút sạc để kết thúc. Tiền trừ vào ví theo điện năng thực tế.</span></div></div></div></div><div class="sticky-action"><div class="action-row"><button class="btn btn-secondary" data-action="directions" data-station="${esc(s.station_id)}">${icon('navigation')} Chỉ đường</button><button class="btn btn-primary" data-go="supportNew" data-station="${esc(s.station_id)}">${icon('alert')} Báo hư hỏng</button></div></div>`), bottomNav('map'));
}

// ---------------------------------------------------------------------------
// Member: my charging session (read only - charging is controlled at the station)
// ---------------------------------------------------------------------------

function liveSessionView(s) {
  const budget = s.max_budget_vnd;
  const progress = budget ? Math.min(100, Math.round((s.cost_vnd / budget) * 100)) : null;
  return `<div class="session-hero"><div class="charging-orb pulse">${icon('bolt')}</div><h2>Đang sạc tại ${esc(s.station_id)}</h2><p>${fDuration(s.duration_seconds)} · bắt đầu ${fDateTime(s.started_at)}</p></div>${budget ? `<div class="info-card"><div class="row"><strong>Ngân sách ${money(budget)}</strong><span class="subtle">${progress}% · còn ${money(s.remaining_budget_vnd ?? Math.max(0, budget - s.cost_vnd))}</span></div><div class="progress" style="margin-top:10px"><i style="width:${progress}%"></i></div></div>` : '<div class="notice">Không chọn ngân sách: phiên chạy tới khi rút sạc hoặc hết số dư.</div>'}<div class="live-grid" style="margin-top:11px"><div class="live-card"><span>Điện năng</span><strong>${kwh(s.energy_kwh)}</strong></div><div class="live-card"><span>Chi phí</span><strong>${money(s.cost_vnd)}</strong></div><div class="live-card"><span>Công suất</span><strong>${(Number(s.power_kw) || 0).toFixed(2)} kW</strong></div><div class="live-card"><span>Đơn giá</span><strong>${money(s.price_per_kwh_vnd)}/kWh</strong></div></div><div class="notice" style="margin-top:12px">${icon('plug')} Để kết thúc, rút sạc tại trạm. Phiên cũng tự dừng khi đạt ngân sách hoặc hết số dư; tiền trừ vào ví theo điện năng thực tế.</div><button class="btn btn-secondary btn-block" style="margin-top:12px" data-go="supportNew" data-station="${esc(s.station_id)}">${icon('alert')} Báo sự cố tại trạm này</button>`;
}

function chargeScreen() {
  let body;
  if (state.session) body = liveSessionView(state.session);
  else {
    const recent = state.data.recent;
    body = `<div class="info-card row"><div class="station-symbol">${icon('plug')}</div><div style="flex:1"><strong>Chưa có phiên đang sạc</strong><div class="subtle">Cắm sạc và quẹt thẻ RFID tại trạm; phiên sẽ hiện ở đây.</div></div></div><button class="btn btn-secondary btn-block" data-tab="map" style="margin-top:10px">${icon('map')} Xem trạm sạc</button><div class="row section-label"><span>Phiên gần đây</span><button class="link-btn" data-go="history">Xem tất cả</button></div>${recent === undefined ? loadingBlock() : recent.length ? `<div class="list-card">${recent.map((h) => historyRow(h, true)).join('')}</div>` : '<p class="subtle">Chưa có phiên sạc nào.</p>'}`;
  }
  return shell(content(`${tabHead('Phiên sạc', state.session ? 'Đang sạc' : '')}${body}`), bottomNav('charge'));
}

function resultScreen() {
  return shell(content(`<div class="row"><h1 class="screen-title">Kết quả phiên</h1><button class="icon-btn" data-tab="charge" aria-label="Đóng">${icon('close')}</button></div>${whenLoaded('session', (s) => {
    const tx = s.transaction_amount_vnd ?? s.cost_vnd;
    const low = s.balance_after_vnd !== null && s.balance_after_vnd !== undefined && s.balance_after_vnd < 20000;
    return `<div class="result-head"><div class="result-icon">${icon('check')}</div><h2>Phiên sạc đã kết thúc</h2><p>Lý do: <strong>${esc(STOP_REASONS[s.stop_reason] || s.stop_reason || '—')}</strong></p></div><div class="receipt"><div class="receipt-total"><span>Đã trừ vào ví</span><strong>${money(tx)}</strong></div>${keyval('Trạm', esc(s.station_id))}${keyval('Điện năng', kwh(s.energy_kwh))}${keyval('Thời lượng', fDuration(s.duration_seconds))}${keyval('Đơn giá', `${money(s.price_per_kwh_vnd)}/kWh`)}${keyval('Ngân sách', s.max_budget_vnd ? money(s.max_budget_vnd) : 'Không đặt')}<div class="divider"></div>${keyval('Số dư còn lại', s.balance_after_vnd !== null && s.balance_after_vnd !== undefined ? money(s.balance_after_vnd) : money(s.balance_vnd))}${keyval('Mã phiên', `<span class="mono">${esc(s.session_id.slice(0, 8))}</span>`)}</div>${low ? `<div class="notice warn" style="margin-top:12px">${icon('wallet')} Số dư thấp, hãy nạp thêm trước lần sạc sau.</div>` : ''}<div class="sticky-action"><div class="action-row"><button class="btn btn-secondary" data-go="history">Lịch sử</button><button class="btn btn-primary" data-tab="map">Về trang chủ</button></div></div>`;
  })}`));
}

const FAULT_STOPS = new Set(['OVER_TEMPERATURE', 'E_STOP', 'SYSTEM_ERROR']);

function fTime(value) {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** "Hôm nay", "Hôm qua", or "Thứ Năm, 02/10". */
function dayLabel(value) {
  const d = new Date(value);
  const today = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(today) - start(d)) / 86400000);
  if (diff === 0) return 'Hôm nay';
  if (diff === 1) return 'Hôm qua';
  const weekday = ['Chủ nhật', 'Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy'][d.getDay()];
  return `${weekday}, ${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}`;
}

function shortDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s} giây`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}g ${pad2(m)}p` : `${m} phút`;
}

/** withDate: lists that are not grouped by day show the date too. */
function historyRow(h, withDate = false) {
  const active = h.status === 'ACTIVE';
  const fault = FAULT_STOPS.has(h.stop_reason);
  const reason = active ? 'Đang sạc' : STOP_REASONS[h.stop_reason] || h.status;
  const tone = active ? 'live' : fault ? 'fault' : '';
  return `<button class="list-row" data-go="sessionDetail" data-session="${esc(h.session_id)}"><span class="row-icon ${tone}">${icon(fault ? 'alert' : 'bolt')}</span><span class="row-main"><strong>${esc(h.station_name || h.station_id)}</strong><span>${withDate ? fDateTime(h.started_at) : fTime(h.started_at)} · ${shortDuration(h.duration_seconds)} · <em class="${tone}">${esc(reason)}</em></span></span><span class="row-side"><strong>${money(h.cost_vnd)}</strong><small>${(Number(h.energy_kwh) || 0).toFixed(4)} kWh</small></span></button>`;
}

/** Sessions grouped by day, newest first. */
function historyGroups(items) {
  const groups = [];
  items.forEach((item) => {
    const label = dayLabel(item.started_at);
    if (groups[groups.length - 1]?.label !== label) groups.push({ label, items: [] });
    groups[groups.length - 1].items.push(item);
  });
  return groups.map((g) => {
    const spent = g.items.reduce((a, s) => a + Number(s.cost_vnd || 0), 0);
    return `<div class="list-group"><div class="list-group-head"><span>${esc(g.label)}</span><span>${g.items.length} phiên · ${money(spent)}</span></div><div class="list-card">${g.items.map(historyRow).join('')}</div></div>`;
  }).join('');
}

function historyScreen() {
  return shell(content(`${pageHead('Lịch sử sạc')}${whenLoaded('sessions', (items) => {
    if (!items.length) return emptyBlock('history', 'Chưa có phiên sạc', 'Phiên sạc của bạn sẽ hiện ở đây.');
    const energy = items.reduce((a, s) => a + Number(s.energy_kwh || 0), 0);
    const spent = items.reduce((a, s) => a + Number(s.cost_vnd || 0), 0);
    return `<div class="stats-grid"><div class="stat-box"><strong>${items.length}</strong><span>Số phiên</span></div><div class="stat-box"><strong>${energy.toFixed(3)}</strong><span>kWh đã sạc</span></div><div class="stat-box"><strong>${money(spent)}</strong><span>Tổng chi</span></div></div>${historyGroups(items)}${state.data.more ? '<button class="btn btn-quiet btn-block" data-action="moreSessions" style="margin-top:12px">Tải thêm</button>' : ''}`;
  })}`));
}

function sessionDetailScreen() {
  return shell(content(`${pageHead('Chi tiết phiên')}${whenLoaded('session', (s) => `<div class="station-hero"><div class="row">${badge(s.status === 'ACTIVE' ? 'Đang sạc' : s.status === 'COMPLETED' ? 'Hoàn tất' : s.status, s.status === 'ACTIVE' ? 'blue' : 'green')}<span class="subtle mono">${esc(s.session_id.slice(0, 8))}</span></div><strong style="font-size:30px;display:block;margin-top:14px">${money(s.cost_vnd)}</strong><div class="subtle">${kwh(s.energy_kwh)} · ${fDuration(s.duration_seconds)}</div></div><div class="info-card" style="margin-top:11px">${keyval('Trạm', esc(s.station_id))}${keyval('Bắt đầu', fDateTime(s.started_at))}${keyval('Kết thúc', fDateTime(s.ended_at))}${keyval('Lý do dừng', esc(STOP_REASONS[s.stop_reason] || s.stop_reason || '—'))}${keyval('Ngân sách', s.max_budget_vnd ? money(s.max_budget_vnd) : 'Không đặt')}${keyval('Đơn giá', `${money(s.price_per_kwh_vnd)}/kWh`)}${s.transaction_id ? keyval('Đã trừ ví', `<span class="negative">-${money(s.transaction_amount_vnd)}</span>`) : ''}${s.balance_after_vnd !== null && s.balance_after_vnd !== undefined ? keyval('Số dư sau', money(s.balance_after_vnd)) : ''}</div><button class="btn btn-secondary btn-block" style="margin-top:12px" data-go="supportNew" data-station="${esc(s.station_id)}">${icon('help')} Báo sự cố về phiên này</button>`)}`));
}

// ---------------------------------------------------------------------------
// Member: wallet
// ---------------------------------------------------------------------------

function transactionRow(t) {
  const credit = t.type === 'TOPUP';
  const title = { TOPUP: 'Nạp tiền', CHARGING: 'Thanh toán phiên sạc' }[t.type] || t.type;
  const after = t.balance_after_vnd !== null && t.balance_after_vnd !== undefined ? `Số dư ${money(t.balance_after_vnd)}` : '';
  return `<button class="list-row" data-go="transactionDetail" data-tx="${esc(t.transaction_id)}"><span class="row-icon ${credit ? 'credit' : ''}">${icon(credit ? 'arrowDown' : 'bolt')}</span><span class="row-main"><strong>${title}</strong><span>${fDateTime(t.created_at)}</span></span><span class="row-side"><strong class="${credit ? 'positive' : t.amount_vnd ? 'negative' : ''}">${txAmount(t)}</strong><small>${after}</small></span></button>`;
}

function walletScreen() {
  const w = state.wallet;
  return shell(content(`${tabHead('Ví của tôi')}<div class="wallet-hero"><span>Số dư khả dụng</span><strong>${w ? money(w.available_balance_vnd) : '…'}</strong><div class="wallet-meta"><div><small>Tổng số dư</small><b>${w ? money(w.balance_vnd) : '…'}</b></div><div><small>Đang giữ</small><b>${w ? money(w.held_balance_vnd) : '…'}</b></div><div><small>Thanh toán</small><b>Ví SECO</b></div></div></div><div class="quick-actions" style="margin-top:13px"><button class="quick" data-go="topup"><i>${icon('plus')}</i>Nạp tiền</button><button class="quick" data-go="transactions"><i>${icon('wallet')}</i>Giao dịch</button><button class="quick" data-go="history"><i>${icon('history')}</i>Phiên sạc</button><button class="quick" data-go="cards"><i>${icon('card')}</i>Thẻ RFID</button></div><div class="row section-label"><span>Giao dịch gần đây</span><button class="link-btn" data-go="transactions">Xem tất cả</button></div>${whenLoaded('transactions', (items) => (items.length ? `<div class="list-card">${items.map(transactionRow).join('')}</div>` : '<p class="subtle">Chưa có giao dịch.</p>'))}`), bottomNav('wallet'));
}

function topupScreen() {
  const amount = state.data.amount ?? 50000;
  const amounts = [10000, 20000, 50000, 100000, 200000, 500000];
  const balance = state.wallet?.balance_vnd ?? 0;
  return shell(content(`${pageHead('Nạp tiền')}<div class="info-card" style="text-align:center;padding:22px 12px"><span class="subtle">Số tiền nạp</span><strong style="font-size:32px;display:block;margin:4px 0">${money(amount)}</strong><span class="subtle">Số dư sau khi nạp: ${money(balance + amount)}</span></div><div class="budget-grid" style="margin-top:13px">${amounts.map((a) => `<button class="budget-choice ${a === amount ? 'active' : ''}" data-action="topupAmount" data-amount="${a}">${money(a)}</button>`).join('')}</div><div class="section-label">Phương thức</div><div class="info-card row" style="border:2px solid var(--brand);background:#f2f8f6"><div class="station-symbol">${icon('wallet')}</div><div style="flex:1"><strong>Sandbox demo</strong><div class="subtle">Nạp thử, không trừ tiền thật</div></div>${icon('check')}</div><div class="notice" style="margin-top:10px">Cổng thanh toán thật nằm ngoài phạm vi dự án.</div><div class="sticky-action"><button class="btn btn-primary btn-block" data-action="topup" data-amount="${amount}">Nạp ${money(amount)}</button></div>`));
}

function transactionsScreen() {
  const filter = state.data.filter || 'ALL';
  return shell(content(`${pageHead('Lịch sử giao dịch')}<div class="tabs">${[['ALL', 'Tất cả'], ['CHARGING', 'Trừ tiền'], ['TOPUP', 'Nạp tiền']].map(([key, label]) => `<button class="${filter === key ? 'active' : ''}" data-action="txFilter" data-filter="${key}">${label}</button>`).join('')}</div>${whenLoaded('transactions', (items) => {
    const shown = filter === 'ALL' ? items : items.filter((t) => t.type === filter);
    return shown.length ? `<div class="list-card">${shown.map(transactionRow).join('')}</div>` : emptyBlock('wallet', 'Không có giao dịch');
  })}`));
}

function transactionDetailScreen() {
  return shell(content(`${pageHead('Chi tiết giao dịch')}${whenLoaded('tx', (t) => {
    const credit = t.type === 'TOPUP';
    return `<div class="info-card" style="text-align:center;padding:24px 13px"><span class="subtle">${credit ? 'Nạp tiền' : 'Thanh toán phiên sạc'}</span><strong style="display:block;font-size:31px;margin:4px 0" class="${credit ? 'positive' : t.amount_vnd ? 'negative' : ''}">${txAmount(t)}</strong>${badge(t.status === 'COMPLETED' ? 'Thành công' : t.status, t.status === 'COMPLETED' ? 'green' : 'orange')}</div><div class="info-card" style="margin-top:11px">${keyval('Mã giao dịch', `<span class="mono">${esc(t.transaction_id.slice(0, 8))}</span>`)}${keyval('Thời gian', fDateTime(t.created_at))}${keyval('Số dư trước', t.balance_before_vnd !== null && t.balance_before_vnd !== undefined ? money(t.balance_before_vnd) : '—')}${keyval('Số dư sau', t.balance_after_vnd !== null && t.balance_after_vnd !== undefined ? money(t.balance_after_vnd) : '—')}</div>${t.session_id ? `<button class="info-card row" data-go="sessionDetail" data-session="${esc(t.session_id)}" style="width:100%;text-align:left;margin-top:11px"><div><strong>Phiên sạc liên quan</strong><div class="subtle mono">${esc(t.session_id.slice(0, 8))}</div></div>${icon('arrow')}</button>` : ''}`;
  })}`));
}

// ---------------------------------------------------------------------------
// Account (member + technician): profile, cards, favourites, support, help
// ---------------------------------------------------------------------------

function accountScreen() {
  const u = state.user;
  const member = isMember();
  const rows = member
    ? [['editProfile', 'user', 'Thông tin cá nhân', ''], ['cards', 'card', 'Thẻ RFID', ''], ['favorites', 'star', 'Trạm yêu thích', String(state.favorites.size)], ['support', 'help', 'Yêu cầu hỗ trợ', ''], ['notifications', 'bell', 'Thông báo', state.unread ? `${state.unread} mới` : ''], ['changePassword', 'key', 'Đổi mật khẩu', ''], ['help', 'help', 'Câu hỏi thường gặp', '']]
    : [['editProfile', 'user', 'Thông tin cá nhân', ''], ['techHistory', 'history', 'Lịch sử công việc', ''], ['cards', 'card', 'Thẻ kỹ thuật viên', ''], ['changePassword', 'key', 'Đổi mật khẩu', '']];
  return shell(content(`${tabHead(member ? 'Tài khoản' : 'Tài khoản kỹ thuật viên')}<div class="profile-head"><div class="profile-avatar">${esc(initials(u.name))}</div><div><h2>${esc(u.name)}</h2><p>${esc(u.email)}<br/>${esc(u.phone || 'Chưa có số điện thoại')}</p></div></div>${member ? '' : '<div class="tech-banner"><div><strong>Kỹ thuật viên SECO</strong><span>Xử lý yêu cầu hỗ trợ và sự cố trạm</span></div><span class="tech-role">TECHNICIAN</span></div>'}<div class="settings">${rows.map(([screen, glyph, label, value]) => `<button class="setting" data-go="${screen}"><span class="setting-icon">${icon(glyph)}</span><span class="setting-main">${label}</span><span class="setting-value">${esc(value)}</span><span class="setting-arrow">${icon('arrow')}</span></button>`).join('')}</div><button class="btn btn-secondary btn-block" data-action="logout" style="margin-top:15px">${icon('logout')} Đăng xuất</button>`), bottomNav('account'));
}

function cardsScreen() {
  const base = isTechnician() ? 'technician' : 'member';
  return shell(content(`${pageHead(isTechnician() ? 'Thẻ kỹ thuật viên' : 'Thẻ RFID')}${whenLoaded('cards', (cards) => {
    if (!cards.length) return emptyBlock('card', 'Chưa có thẻ', 'Liên hệ quầy SECO để được cấp thẻ RFID.');
    return cards.map((c) => {
      const [label, tone] = CARD_STATUS[c.status] || [c.status, 'gray'];
      const actions = c.status === 'ACTIVE'
        ? `<button class="btn btn-secondary btn-compact" data-action="cardAction" data-card="${esc(c.card_id)}" data-op="lock" data-base="${base}">${icon('lock')} Tạm khoá</button><button class="btn btn-danger btn-compact" data-action="cardAction" data-card="${esc(c.card_id)}" data-op="report-lost" data-base="${base}">${icon('shield')} Báo mất</button>`
        : c.status === 'LOCKED'
          ? `<button class="btn btn-primary btn-compact" data-action="cardAction" data-card="${esc(c.card_id)}" data-op="unlock" data-base="${base}">${icon('unlock')} Mở khoá</button><button class="btn btn-danger btn-compact" data-action="cardAction" data-card="${esc(c.card_id)}" data-op="report-lost" data-base="${base}">${icon('shield')} Báo mất</button>`
          : '';
      const hint = { LOST: 'Thẻ bị vô hiệu vĩnh viễn. Tới quầy để được cấp thẻ mới.', INACTIVE: 'Thẻ đã bị quầy vô hiệu hoá. Liên hệ quầy để được hỗ trợ.', LOCKED: 'Thẻ đang tạm khoá, không quẹt sạc được. Nếu quầy khoá, chỉ quầy mở được.', PENDING: 'Thẻ đang chờ quét UID tại quầy.' }[c.status];
      return `<div class="rfid-card ${c.status === 'ACTIVE' ? '' : 'off'}"><div class="row"><small>${icon('bolt')} SECO ${isTechnician() ? 'TECHNICIAN' : 'MEMBER'}</small>${badge(label, tone)}</div><strong>•••• ${esc(c.uid_suffix || '----')}</strong><small>${esc(c.label || c.card_id)} · cấp ${fDateTime(c.created_at)}</small></div>${hint ? `<p class="subtle" style="margin:-8px 0 10px">${hint}</p>` : ''}${actions ? `<div class="action-row" style="margin:-4px 0 18px">${actions}</div>` : ''}`;
    }).join('') + '<div class="notice">Mất thẻ? Báo mất ngay để không ai dùng thẻ của bạn để sạc. Phiên đang sạc vẫn tiếp tục cho tới khi bạn dừng.</div>';
  })}`));
}

function favoritesScreen() {
  const list = state.stations.filter((s) => state.favorites.has(s.station_id));
  return shell(content(`${pageHead('Trạm yêu thích')}${list.length ? `<div class="station-list">${list.map(stationRow).join('')}</div>` : emptyBlock('star', 'Chưa có trạm yêu thích', 'Bấm ngôi sao ở trang chi tiết trạm để lưu lại.')}`));
}

function ticketRow(t, go = 'supportDetail') {
  const [label, tone] = TICKET_STATUS[t.status] || [t.status, 'gray'];
  return `<button class="ticket-row" data-go="${go}" data-ticket="${esc(t.ticket_id)}"><div class="row"><strong>${esc(t.station_name || t.station_id)}</strong>${badge(label, tone)}</div><span>${esc(t.category_label || TICKET_CATEGORIES[t.category] || t.category)}${t.user_name && go === 'ticketDetail' ? ` · ${esc(t.user_name)}` : ''}</span><small>${fAgo(t.created_at)}${t.assigned_name ? ` · KTV ${esc(t.assigned_name)}` : ''}</small></button>`;
}

function supportScreen() {
  return shell(content(`${pageHead('Yêu cầu hỗ trợ', `<button class="icon-btn" data-go="supportNew" aria-label="Tạo yêu cầu">${icon('plus')}</button>`)}${whenLoaded('tickets', (page) => (page.items.length ? `<div class="stack">${page.items.map((t) => ticketRow(t)).join('')}</div>` : emptyBlock('help', 'Chưa có yêu cầu', 'Gặp sự cố tại trạm? Gửi yêu cầu, kỹ thuật viên sẽ nhận và xử lý.')))}<div class="sticky-action"><button class="btn btn-primary btn-block" data-go="supportNew">${icon('plus')} Gửi yêu cầu hỗ trợ</button></div>`));
}

function supportNewScreen({ station = '' }) {
  const stations = state.stations;
  return shell(content(`${pageHead('Báo trạm hư hỏng')}<form class="stack" data-form="supportNew"><div class="field"><label>Trạm gặp sự cố</label><select name="station_id" class="select" required><option value="">Chọn trạm</option>${stations.map((s) => `<option value="${esc(s.station_id)}" ${s.station_id === station ? 'selected' : ''}>${esc(s.station_id)} · ${esc(s.name)}</option>`).join('')}</select></div><div class="field"><label>Vấn đề</label><select name="category" class="select" required>${Object.entries(TICKET_CATEGORIES).map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select></div><div class="field"><label>Mô tả (tuỳ chọn)</label><textarea name="description" maxlength="1000" placeholder="Ví dụ: quẹt thẻ nhưng trạm không phản hồi"></textarea></div><div class="notice">Trạng thái của trạm lúc bạn gửi được ghi lại kèm yêu cầu. Kỹ thuật viên nhận thông báo ngay; bạn được báo khi có người nhận và khi xử lý xong.</div><button class="btn btn-primary btn-block">${icon('send')} Gửi yêu cầu</button></form>`));
}

function supportDetailScreen() {
  return shell(content(`${pageHead('Chi tiết yêu cầu')}${whenLoaded('ticket', (t) => {
    const [label, tone] = TICKET_STATUS[t.status] || [t.status, 'gray'];
    const steps = [
      ['Đã gửi', fDateTime(t.created_at), true],
      [t.assigned_name ? `${t.assigned_name} đã nhận` : 'Chờ kỹ thuật viên nhận', fDateTime(t.assigned_at), Boolean(t.assigned_at)],
      [t.status === 'CANCELLED' ? 'Đã huỷ' : 'Đã xử lý', fDateTime(t.resolved_at || t.cancelled_at), Boolean(t.resolved_at || t.cancelled_at)]
    ];
    return `<div class="station-hero"><div class="row">${badge(label, tone)}<span class="subtle mono">${esc(t.ticket_id)}</span></div><h2>${esc(t.station_name || t.station_id)}</h2><p>${esc(t.category_label)}</p>${t.description ? `<p style="margin-top:8px;color:var(--ink)">${esc(t.description)}</p>` : ''}</div><div class="section-label">Tiến trình</div><div class="timeline">${steps.map(([title, time, done]) => `<div class="tl ${done ? 'done' : ''}"><i></i><div><strong>${esc(title)}</strong><span>${done ? time : ''}</span></div></div>`).join('')}</div>${t.resolution_note ? `<div class="section-label">Kết quả xử lý</div><div class="info-card">${esc(t.resolution_note)}</div>` : ''}${t.status === 'OPEN' ? '<div class="sticky-action"><button class="btn btn-danger btn-block" data-action="cancelTicket">Huỷ yêu cầu</button></div>' : ''}`;
  })}`));
}

const FAQ = [
  ['Làm sao để bắt đầu sạc?', 'Tới một trạm đang "Sẵn sàng", cắm sạc vào xe rồi quẹt thẻ RFID. Trạm hiện tên và số dư; bạn có thể gạt joystick chọn ngân sách, để yên thì trạm tự sạc sau vài giây.'],
  ['Phiên sạc kết thúc khi nào?', 'Khi bạn rút sạc, đạt ngân sách đã chọn trên trạm, hết số dư, hoặc trạm gặp sự cố an toàn (quá nhiệt, quá dòng). Tiền được trừ vào ví theo điện năng thực tế.'],
  ['Theo dõi phiên sạc ở đâu?', 'Tab "Phiên sạc" hiện điện năng, chi phí và thời gian theo thời gian thực. Lịch sử phiên và giao dịch nằm ở tab "Ví".'],
  ['Mất thẻ RFID thì làm gì?', 'Vào Tài khoản → Thẻ RFID → Báo mất. Thẻ bị vô hiệu ngay; tới quầy SECO để được cấp thẻ mới. Nếu chỉ tạm để quên, dùng "Tạm khoá" và tự mở lại sau.'],
  ['Trạm hư hỏng hoặc không phản hồi?', 'Bấm "Báo trạm hư hỏng" ở trang trạm. Kỹ thuật viên nhận thông báo kèm trạng thái của trạm; bạn được báo khi có người nhận và khi xử lý xong.']
];

function helpScreen() {
  const open = state.data.open ?? 0;
  return shell(content(`${pageHead('Câu hỏi thường gặp')}<div class="help-list">${FAQ.map(([q, a], i) => `<button class="help-row" data-action="faq" data-index="${i}"><span>${esc(q)}</span>${icon(open === i ? 'close' : 'arrow')}</button>${open === i ? `<div class="faq-answer">${esc(a)}</div>` : ''}`).join('')}</div><div class="info-card row" style="margin-top:14px"><div><strong>Cần hỗ trợ tại trạm?</strong><div class="subtle">Gửi yêu cầu cho kỹ thuật viên.</div></div><button class="btn btn-primary btn-compact" data-go="supportNew">Gửi yêu cầu</button></div>`));
}

// ---------------------------------------------------------------------------
// Notifications (every role)
// ---------------------------------------------------------------------------

function notificationsScreen() {
  const readAll = state.unread ? '<button class="link-btn" data-action="readAll">Đọc hết</button>' : '';
  return shell(content(`${isTechnician() ? tabHead('Thông báo', '', readAll) : pageHead('Thông báo', readAll)}${whenLoaded('notifications', (page) => (page.items.length ? `<div class="stack">${page.items.map((n) => `<button class="notification ${n.read_at ? '' : 'unread'}" data-action="openNotification" data-id="${esc(n.notification_id)}"><span class="notification-icon">${icon(NOTIFICATION_ICONS[n.type] || 'bell')}</span><span class="notification-main"><strong>${esc(n.title)}</strong><span>${esc(n.body)}</span><small>${fAgo(n.created_at)}</small></span></button>`).join('')}</div>${page.total > page.items.length ? '<button class="btn btn-quiet btn-block" data-action="moreNotifications" style="margin-top:10px">Tải thêm</button>' : ''}` : emptyBlock('bell', 'Chưa có thông báo')))}`), isTechnician() ? bottomNav('notifications') : '');
}

// ---------------------------------------------------------------------------
// Technician
// ---------------------------------------------------------------------------

function techRequestsScreen() {
  const view = state.data.view || 'OPEN';
  const counts = state.data.counts || {};
  return shell(content(`${tabHead('Yêu cầu hỗ trợ', `Xin chào, ${state.user.name.split(' ').slice(-1)[0]}`)}<div class="stats-grid"><div class="stat-box"><strong>${counts.open ?? '…'}</strong><span>Đang chờ nhận</span></div><div class="stat-box"><strong>${counts.mine ?? '…'}</strong><span>Tôi đang xử lý</span></div><div class="stat-box"><strong>${counts.stations ?? '…'}</strong><span>Trạm cần đến</span></div></div><div class="tabs" style="margin-top:13px">${[['OPEN', 'Đang chờ'], ['MINE', 'Của tôi'], ['DONE', 'Đã xong']].map(([key, label]) => `<button class="${view === key ? 'active' : ''}" data-action="techView" data-view="${key}">${label}</button>`).join('')}</div>${whenLoaded('tickets', (page) => (page.items.length ? `<div class="stack">${page.items.map((t) => ticketRow(t, 'ticketDetail')).join('')}</div>` : emptyBlock('inbox', view === 'OPEN' ? 'Không có yêu cầu đang chờ' : view === 'MINE' ? 'Bạn chưa nhận yêu cầu nào' : 'Chưa có yêu cầu đã xử lý')))}`), bottomNav('techRequests'));
}

function telemetryGrid(t) {
  if (!t) return '<p class="subtle">Chưa có telemetry.</p>';
  const cell = (label, value) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`;
  return `<div class="telemetry">${cell('Nhiệt độ', t.temperature_c !== undefined && t.temperature_c !== null ? `${Number(t.temperature_c).toFixed(1)}°C` : '—')}${cell('Dòng điện', t.current_a !== undefined && t.current_a !== null ? `${Number(t.current_a).toFixed(2)} A` : '—')}${cell('Điện áp', t.voltage_v !== undefined && t.voltage_v !== null ? `${Number(t.voltage_v).toFixed(2)} V` : '—')}${cell('Công suất', t.power_kw !== undefined && t.power_kw !== null ? `${Number(t.power_kw).toFixed(3)} kW` : '—')}${cell('Relay', t.relay === undefined ? '—' : t.relay ? 'ĐÓNG' : 'MỞ')}${cell('Đầu sạc', t.plugged === undefined ? '—' : t.plugged ? 'Đã cắm' : 'Chưa cắm')}</div>`;
}

function errorRow(e) {
  const [label, tone] = SEVERITY[e.severity] || [e.severity, 'red'];
  return `<div class="fault-card"><div class="row" style="margin-top:0"><h3>${esc(ERROR_TYPES[e.error_type] || e.error_type)}</h3>${badge(label, tone)}</div><p>${esc(e.message)}</p><small class="subtle">${fDateTime(e.occurred_at)}</small></div>`;
}

function ticketDetailScreen() {
  return shell(content(`${pageHead('Yêu cầu hỗ trợ')}${whenLoaded('ticket', (t) => {
    const [label, tone] = TICKET_STATUS[t.status] || [t.status, 'gray'];
    const snap = t.station_snapshot || {};
    const mine = t.assigned_to === state.user.user_id;
    let action = '';
    if (t.status === 'OPEN') action = `<button class="btn btn-primary btn-block" data-action="acceptTicket">${icon('check')} Nhận xử lý</button>`;
    else if (t.status === 'IN_PROGRESS' && mine) {
      const live = state.data.station;
      const simulated = Boolean(live?.is_simulated);
      const down = ['ERROR', 'MAINTENANCE'].includes(live?.status);
      const head = `<div class="live-station ${live?.status === 'ERROR' ? 'broken' : ''}"><span>Trạm hiện tại</span>${availabilityBadge(live?.availability || 'OFFLINE')}${simulated ? badge('Trạm mô phỏng', 'gray') : badge('Trạm thật', 'blue')}<button type="button" class="link-btn" data-action="reload">Làm mới</button></div>`;
      const form = (hint, label, placeholder) => `${hint}<form class="stack" data-form="resolveTicket"><div class="field"><label>Kết quả xử lý</label><textarea name="resolution_note" required maxlength="1000" placeholder="${placeholder}"></textarea></div><button class="btn btn-primary btn-block">${icon('check')} ${label}</button></form>`;
      if (simulated && down) {
        // No hardware: the technician's confirmation puts the station back to READY.
        action = head + form(`<div class="notice">${icon('wrench')} Trạm mô phỏng, không có phần cứng: <strong>xác nhận đã sửa</strong> sẽ đưa trạm về <strong>Sẵn sàng</strong>, đóng các lỗi đang mở và ghi vào lịch sử bảo trì của bạn.</div>`, 'Xác nhận đã sửa', 'Ví dụ: đã thay module nguồn, trạm hoạt động lại');
      } else if (live?.status === 'ERROR') {
        action = `${head}<div class="notice warn">${icon('wrench')} Trạm thật vẫn đang <strong>LỖI</strong>. Tại trạm: quẹt <strong>thẻ kỹ thuật viên</strong> + giữ <strong>STOP 3 giây</strong> để vào bảo trì; sửa xong làm lại để đưa trạm về <strong>Sẵn sàng</strong>. Sau đó mới hoàn tất yêu cầu.</div><button class="btn btn-quiet btn-block" disabled style="margin-top:8px">Hoàn tất khi trạm hết lỗi</button>`;
      } else {
        action = head + form('', 'Hoàn tất yêu cầu', live?.status === 'MAINTENANCE' ? 'Ví dụ: trạm tạm bảo trì, chờ thay cảm biến' : 'Ví dụ: đã thay cảm biến, trạm sạc lại bình thường');
      }
    }
    else if (t.status === 'IN_PROGRESS') action = `<div class="notice">${esc(t.assigned_name)} đang xử lý yêu cầu này.</div>`;
    return `<div class="station-hero"><div class="row">${badge(label, tone)}<span class="subtle">${fAgo(t.created_at)}</span></div><h2>${esc(t.category_label)}</h2><p>${esc(t.station_name || t.station_id)} · ${esc(t.station_id)}</p>${t.description ? `<p style="margin-top:8px;color:var(--ink)">${esc(t.description)}</p>` : ''}</div><div class="section-label">Member</div><div class="info-card row"><div class="profile-avatar small">${esc(initials(t.user_name))}</div><div style="flex:1"><strong>${esc(t.user_name || '—')}</strong><div class="subtle">${esc(t.user_phone || 'Chưa có số điện thoại')}</div></div>${t.user_phone ? `<a class="icon-btn" href="tel:${esc(t.user_phone.replace(/[^0-9+]/g, ''))}" aria-label="Gọi">${icon('phone')}</a>` : ''}</div><div class="row section-label"><span>Trạm lúc gửi yêu cầu</span><button class="link-btn" data-go="techStationDetail" data-station="${esc(t.station_id)}">Xem trạm hiện tại</button></div><div class="info-card">${keyval('Trạng thái', `${availabilityBadge(snap.availability || (snap.is_online === false ? 'OFFLINE' : snap.status))}`)}${keyval('Kết nối', snap.is_online ? 'Online' : 'Offline')}${keyval('Đầu sạc', LOCK_LABEL[snap.connector_lock] || '—')}${keyval('Heartbeat', fDateTime(snap.last_heartbeat_at))}${t.session_id ? keyval('Phiên liên quan', `<span class="mono">${esc(t.session_id.slice(0, 8))}</span>`) : ''}</div><div style="margin-top:9px">${telemetryGrid(snap.latest_telemetry)}</div>${(snap.open_errors || []).length ? `<div class="section-label">Lỗi đang mở lúc đó</div><div class="stack">${snap.open_errors.map(errorRow).join('')}</div>` : ''}${t.resolution_note ? `<div class="section-label">Kết quả xử lý</div><div class="info-card">${esc(t.resolution_note)}<div class="subtle" style="margin-top:6px">${esc(t.assigned_name)} · ${fDateTime(t.resolved_at)}</div></div>` : ''}${action ? `<div class="sticky-action">${action}</div>` : ''}`;
  })}`), bottomNav('techRequests'));
}

function techStationsScreen() {
  return shell(content(`${tabHead('Trạm sạc', 'Ưu tiên trạm cần đến')}${whenLoaded('stations', (items) => `<div class="station-list">${items.map((s) => `<button class="station-row" data-go="techStationDetail" data-station="${esc(s.station_id)}"><div class="station-symbol ${s.availability === 'ERROR' ? 'danger' : ''}">${icon(s.availability === 'ERROR' ? 'alert' : 'bolt')}</div><div class="station-text"><h3>${esc(s.station_id)} · ${esc(s.name)}</h3><p>${s.open_error_count ? `${s.open_error_count} lỗi mở · ` : ''}${s.open_ticket_count ? `${s.open_ticket_count} yêu cầu · ` : ''}${s.temperature_c !== null && s.temperature_c !== undefined ? `${Number(s.temperature_c).toFixed(1)}°C · ` : ''}${fAgo(s.last_heartbeat_at)}</p></div>${availabilityBadge(s.availability)}</button>`).join('')}</div>`)}`), bottomNav('techStations'));
}

function techStationDetailScreen() {
  return shell(content(`${pageHead('Chi tiết trạm')}${whenLoaded('detail', (d) => {
    const s = d.station;
    return `<div class="station-hero">${availabilityBadge(s.availability)}<h2>${esc(s.station_id)} · ${esc(s.name)}</h2><p>${icon('pin')} ${esc(s.address || 'Chưa có địa chỉ')}</p><div class="metric-grid"><div class="metric"><span>Trạng thái</span><strong>${esc(s.status)}</strong></div><div class="metric"><span>Đầu sạc</span><strong>${LOCK_LABEL[s.connector_lock] || '—'}</strong></div><div class="metric"><span>Heartbeat</span><strong>${fAgo(s.last_heartbeat_at)}</strong></div></div></div><button class="btn btn-secondary btn-block" style="margin-top:10px" data-action="directions" data-station="${esc(s.station_id)}" data-lat="${s.latitude ?? ''}" data-lng="${s.longitude ?? ''}">${icon('navigation')} Chỉ đường tới trạm</button><div class="section-label">Telemetry mới nhất</div>${telemetryGrid(d.latest_telemetry)}<div class="section-label">Lỗi đang mở (${d.open_errors.length})</div>${d.open_errors.length ? `<div class="stack">${d.open_errors.map(errorRow).join('')}</div>` : '<p class="subtle">Không có lỗi đang mở.</p>'}<div class="section-label">Yêu cầu hỗ trợ đang mở (${d.open_tickets.length})</div>${d.open_tickets.length ? `<div class="stack">${d.open_tickets.map((t) => ticketRow(t, 'ticketDetail')).join('')}</div>` : '<p class="subtle">Không có yêu cầu.</p>'}<div class="section-label">Bảo trì ${s.is_simulated ? badge('Trạm mô phỏng', 'gray') : badge('Trạm thật', 'blue')}</div><div class="notice">${icon('wrench')} ${s.is_simulated ? 'Trạm mô phỏng không có phần cứng: nhận yêu cầu hỗ trợ của trạm rồi bấm <strong>Xác nhận đã sửa</strong> để đưa trạm về Sẵn sàng.' : 'Vào / ra bảo trì ngay tại trạm: quẹt <strong>thẻ kỹ thuật viên</strong> rồi <strong>giữ nút STOP 3 giây</strong>. Trạm chỉ về READY khi đã an toàn (nhiệt độ dưới ngưỡng).'}</div>${d.maintenance.length ? `<div class="list-card" style="margin-top:10px">${d.maintenance.map(maintenanceRow).join('')}</div>` : ''}`;
  })}`), bottomNav('techStations'));
}

function maintenanceRow(m) {
  const done = m.status === 'COMPLETED';
  const label = done ? 'Đã hoàn tất' : m.status === 'IN_PROGRESS' ? 'Đang bảo trì' : 'Đã huỷ';
  return `<div class="list-row"><span class="row-icon ${done ? '' : 'live'}">${icon('wrench')}</span><span class="row-main"><strong>${esc(m.station_id)} · ${label}</strong><span>${esc(m.technician_name || '')} · ${fDateTime(m.started_at)}${done ? ` → ${fTime(m.completed_at)}` : ''}</span></span></div>`;
}

function techHistoryScreen() {
  const tab = state.data.tab || 'TICKETS';
  return shell(content(`${pageHead('Lịch sử công việc')}<div class="tabs" style="grid-template-columns:1fr 1fr">${[['TICKETS', 'Yêu cầu đã xử lý'], ['MAINTENANCE', 'Bảo trì tại trạm']].map(([key, label]) => `<button class="${tab === key ? 'active' : ''}" data-action="historyTab" data-tab-key="${key}">${label}</button>`).join('')}</div>${tab === 'TICKETS'
    ? whenLoaded('tickets', (page) => (page.items.length ? `<div class="stack">${page.items.map((t) => ticketRow(t, 'ticketDetail')).join('')}</div>` : emptyBlock('inbox', 'Chưa có yêu cầu đã xử lý')))
    : whenLoaded('maintenance', (page) => (page.items.length ? `<div class="list-card">${page.items.map(maintenanceRow).join('')}</div>` : emptyBlock('wrench', 'Chưa có lần bảo trì nào', 'Mỗi lần bạn vào / ra bảo trì bằng thẻ tại trạm được ghi lại ở đây.')))}`));
}

// ---------------------------------------------------------------------------
// Screen registry
// ---------------------------------------------------------------------------

const SCREENS = {
  login: { view: loginScreen },
  forgotPassword: { view: forgotPasswordScreen },
  resetPassword: { view: resetPasswordScreen },
  changePassword: { view: changePasswordScreen },
  editProfile: { view: editProfileScreen },
  notifications: {
    view: notificationsScreen,
    load: async () => { state.data.notifications = await api('/notifications', { query: { limit: 30 } }); state.unread = state.data.notifications.unread_count; }
  },
  account: { view: accountScreen },
  cards: {
    view: cardsScreen,
    load: async () => { state.data.cards = await api(`/${isTechnician() ? 'technician' : 'member'}/rfid-cards`); }
  },
  // member
  map: { view: mapScreen },
  stations: { view: stationsScreen },
  stationDetail: { view: stationDetailScreen, load: async () => { if (!state.wallet) await refreshWallet(); } },
  charge: {
    view: chargeScreen,
    load: async () => {
      await refreshChargingState();
      if (!state.session) state.data.recent = (await api('/member/sessions', { query: { limit: 3 } })) || [];
    }
  },
  result: { view: resultScreen, load: async ({ session }) => { state.data.session = await api(`/member/charging/${encodeURIComponent(session)}`); void refreshWallet(); } },
  history: { view: historyScreen, load: async () => { const items = await api('/member/sessions', { query: { limit: 20 } }); state.data.sessions = items; state.data.more = items.length === 20; } },
  sessionDetail: { view: sessionDetailScreen, load: async ({ session }) => { state.data.session = await api(`/member/charging/${encodeURIComponent(session)}`); } },
  wallet: { view: walletScreen, load: async () => { await refreshWallet(); state.data.transactions = await api('/member/transactions', { query: { limit: 5 } }); } },
  topup: { view: topupScreen, load: async () => { await refreshWallet(); } },
  transactions: { view: transactionsScreen, load: async () => { state.data.transactions = await api('/member/transactions', { query: { limit: 100 } }); } },
  transactionDetail: { view: transactionDetailScreen, load: async ({ tx }) => { state.data.tx = await api(`/member/transactions/${encodeURIComponent(tx)}`); } },
  favorites: { view: favoritesScreen },
  support: { view: supportScreen, load: async () => { state.data.tickets = await api('/member/support-tickets'); } },
  supportNew: { view: supportNewScreen },
  supportDetail: { view: supportDetailScreen, load: async ({ ticket }) => { state.data.ticket = await api(`/member/support-tickets/${encodeURIComponent(ticket)}`); } },
  help: { view: helpScreen },
  // technician
  techRequests: { view: techRequestsScreen, load: loadTechRequests },
  ticketDetail: {
    view: ticketDetailScreen,
    load: async ({ ticket }) => {
      const t = await api(`/technician/support-tickets/${encodeURIComponent(ticket)}`);
      state.data.station = await apiOrNull(`/stations/${encodeURIComponent(t.station_id)}`);
      state.data.ticket = t;
    }
  },
  techStations: { view: techStationsScreen, load: async () => { state.data.stations = await api('/technician/stations'); } },
  techStationDetail: { view: techStationDetailScreen, load: async ({ station }) => { state.data.detail = await api(`/technician/stations/${encodeURIComponent(station)}`); } },
  techHistory: { view: techHistoryScreen, load: loadTechHistory }
};

async function loadTechRequests() {
  const view = state.data.view || 'OPEN';
  const query = view === 'OPEN' ? { status: 'OPEN' } : view === 'MINE' ? { scope: 'mine', status: 'IN_PROGRESS' } : { scope: 'mine', status: 'RESOLVED' };
  const [tickets, open, mine, stations] = await Promise.all([
    api('/technician/support-tickets', { query }),
    api('/technician/support-tickets', { query: { status: 'OPEN', limit: 1 } }),
    api('/technician/support-tickets', { query: { scope: 'mine', status: 'IN_PROGRESS', limit: 1 } }),
    api('/technician/stations')
  ]);
  state.data.tickets = tickets;
  state.data.counts = {
    open: open.total,
    mine: mine.total,
    stations: stations.filter((s) => ['ERROR', 'MAINTENANCE', 'OFFLINE'].includes(s.availability) || s.open_ticket_count).length
  };
}

async function loadTechHistory() {
  if ((state.data.tab || 'TICKETS') === 'TICKETS') state.data.tickets = await api('/technician/support-tickets', { query: { scope: 'mine', status: 'RESOLVED' } });
  else state.data.maintenance = await api('/technician/maintenance');
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function render() {
  const screen = SCREENS[state.screen] || SCREENS.login;
  appRoot.innerHTML = screen.view(state.params);
  window.requestAnimationFrame(() => {
    refreshIcons();
    if (state.screen === 'map') initMap();
    updateCountdowns();
  });
}

function refreshIcons() {
  if (window.lucide?.createIcons) window.lucide.createIcons();
}

/** Patch the unread badge without re-rendering (keeps what the user is typing). */
function updateBadges() {
  document.querySelectorAll('.bell-count').forEach((el) => {
    el.hidden = !state.unread;
    el.textContent = state.unread > 9 ? '9+' : String(state.unread);
  });
}

function updateCountdowns() {
  document.querySelectorAll('[data-countdown]').forEach((el) => {
    const left = Math.max(0, Math.floor((new Date(el.dataset.countdown).getTime() - Date.now()) / 1000));
    el.textContent = `${pad2(Math.floor(left / 60))}:${pad2(left % 60)}`;
  });
}

function toast(message, tone = '') {
  document.querySelector('.toast')?.remove();
  const node = document.createElement('div');
  node.className = `toast ${tone}`;
  node.textContent = message;
  document.body.appendChild(node);
  window.setTimeout(() => node.remove(), 2800);
}

// ---------------------------------------------------------------------------
// Map (member)
// ---------------------------------------------------------------------------

let leafletMap = null;

function initMap() {
  const element = document.querySelector('#station-map');
  if (!element || !window.L) return;
  if (leafletMap) leafletMap.remove();
  const shown = state.stations.filter((s) => Number.isFinite(s.latitude) && Number.isFinite(s.longitude));
  leafletMap = window.L.map(element, { zoomControl: false, attributionControl: false }).setView([16.0544, 108.2022], 13);
  window.L.control.zoom({ position: 'bottomright' }).addTo(leafletMap);
  window.L.control.attribution({ prefix: false, position: 'bottomleft' }).addAttribution('© OpenStreetMap').addTo(leafletMap);
  window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(leafletMap);
  shown.forEach((station) => {
    const tone = (AVAILABILITY[station.availability] || ['', 'gray'])[1];
    const match = stationMatches(station);
    const marker = window.L.circleMarker([station.latitude, station.longitude], {
      radius: state.favorites.has(station.station_id) ? 12 : 10,
      color: '#fff', weight: 3, fillColor: MAP_COLORS[tone], fillOpacity: match ? 1 : 0.2, opacity: match ? 1 : 0.3
    });
    marker.bindTooltip(`${station.station_id} · ${(AVAILABILITY[station.availability] || [station.availability])[0]}`, { direction: 'top', offset: [0, -8] });
    marker.on('click', () => {
      state.mapSelected = station.station_id;
      leafletMap.setView([station.latitude, station.longitude], Math.max(leafletMap.getZoom(), 16), { animate: true });
      const wrap = document.querySelector('.map-wrap');
      wrap?.classList.add('has-selection');
      wrap?.classList.remove('full-map');
      document.querySelector('#station-sheet').innerHTML = stationSheet(station);
      refreshIcons();
      window.requestAnimationFrame(() => leafletMap?.invalidateSize({ animate: false }));
    });
    marker.addTo(leafletMap);
  });
  const focus = state.mapSelected ? stationById(state.mapSelected) : null;
  if (focus) leafletMap.setView([focus.latitude, focus.longitude], 16);
  else if (shown.length > 1) leafletMap.fitBounds(window.L.latLngBounds(shown.map((s) => [s.latitude, s.longitude])).pad(0.18), { maxZoom: 14 });
  if (state.userLocation) {
    window.L.circleMarker([state.userLocation.lat, state.userLocation.lng], { radius: 8, color: '#fff', weight: 3, fillColor: '#256fc1', fillOpacity: 1 })
      .bindTooltip('Vị trí của bạn').addTo(leafletMap);
  }
}

function watchLocation() {
  if (!navigator.geolocation) return;
  navigator.geolocation.watchPosition((position) => {
    const first = !state.userLocation;
    state.userLocation = { lat: position.coords.latitude, lng: position.coords.longitude };
    if (first && ['map', 'stations', 'stationDetail'].includes(state.screen)) render();
  }, () => null, { enableHighAccuracy: true, maximumAge: 30000, timeout: 10000 });
}

function openDirections(station) {
  if (!station || !Number.isFinite(Number(station.latitude))) return toast('Trạm chưa có toạ độ để chỉ đường');
  const params = new URLSearchParams({ api: '1', destination: `${station.latitude},${station.longitude}`, travelmode: 'driving' });
  if (state.userLocation) params.set('origin', `${state.userLocation.lat},${state.userLocation.lng}`);
  window.open(`https://www.google.com/maps/dir/?${params}`, '_blank', 'noopener,noreferrer');
}

// ---------------------------------------------------------------------------
// Data refresh
// ---------------------------------------------------------------------------

async function refreshStations() {
  try {
    state.stations = (await api('/stations')).map((s) => ({ ...s, latitude: Number(s.latitude), longitude: Number(s.longitude) }));
    if (['map', 'stations', 'stationDetail', 'favorites', 'supportNew'].includes(state.screen) && !document.activeElement?.matches('input, textarea, select')) render();
  } catch { /* keep the last list */ }
}

async function refreshWallet() {
  if (!isMember()) return;
  state.wallet = await api('/member/wallet');
}

async function refreshFavorites() {
  if (!isMember()) return;
  const items = await api('/member/favorite-stations').catch(() => []);
  state.favorites = new Set(items.map((s) => s.station_id));
}

async function refreshChargingState() {
  if (!isMember()) return;
  const hadSession = state.session;
  const session = await apiOrNull('/member/charging/current');
  state.session = session;
  // The session ended while we were not listening (station unplugged, budget reached...).
  if (hadSession && !session && state.screen === 'charge') go('result', { session: hadSession.session_id }, { replace: true });
}

async function refreshUnread() {
  try { state.unread = (await api('/notifications/unread-count')).unread_count; updateBadges(); } catch { /* offline */ }
}

let sessionPoller = null;
/** Fallback when the WebSocket is down: notice a session starting / ticking / ending. */
function startPolling() {
  window.clearInterval(sessionPoller);
  sessionPoller = window.setInterval(async () => {
    if (!isMember() || state.ws?.readyState === WebSocket.OPEN) return;
    if (!state.session && state.screen !== 'charge') return;
    const before = JSON.stringify([state.session?.session_id, state.session?.cost_vnd]);
    try { await refreshChargingState(); } catch { return; }
    const after = JSON.stringify([state.session?.session_id, state.session?.cost_vnd]);
    if (before !== after && state.screen === 'charge') void loadScreen();
  }, cfg.sessionPollIntervalMs);
}

// ---------------------------------------------------------------------------
// Realtime WebSocket
// ---------------------------------------------------------------------------

function connectRealtime() {
  const token = tokenStore.get();
  if (!token || state.ws) return;
  const url = new URL(cfg.wsPath, window.location.href);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('token', token);
  const ws = new WebSocket(url);
  state.ws = ws;
  ws.onopen = () => { state.wsRetry = 0; };
  ws.onmessage = (event) => { try { onRealtime(JSON.parse(event.data)); } catch { /* ignore malformed */ } };
  ws.onclose = () => {
    if (state.ws !== ws) return; // replaced (password change) or signed out
    state.ws = null;
    if (!state.user) return;
    state.wsRetry = Math.min(state.wsRetry + 1, 6);
    window.setTimeout(connectRealtime, 1000 * 2 ** state.wsRetry);
  };
}

/** Screens whose data a notification type changes (they reload when it arrives). */
const SCREENS_FOR_NOTIFICATION = {
  CARD_ASSIGNED: ['cards', 'account'],
  CARD_BLOCKED: ['cards', 'account'],
  CARD_UNLOCKED: ['cards', 'account'],
  TOPUP_SUCCEEDED: ['wallet', 'transactions'],
  CHARGING_COMPLETED: ['wallet', 'transactions', 'history', 'charge'],
  LOW_BALANCE: ['wallet'],
  SUPPORT_TICKET_ACCEPTED: ['support', 'supportDetail'],
  SUPPORT_TICKET_RESOLVED: ['support', 'supportDetail'],
  SUPPORT_TICKET_CREATED: ['techRequests'],
  STATION_ERROR: ['techStations', 'techStationDetail', 'techRequests', 'ticketDetail']
};

function onRealtime(event) {
  if (event.type === 'notification') {
    const n = event.notification;
    state.unread += 1;
    updateBadges();
    toast(n.title);
    if ('Notification' in window && window.Notification.permission === 'granted' && document.hidden) new window.Notification(n.title, { body: n.body, tag: n.notification_id });
    if (state.screen === 'notifications' || (SCREENS_FOR_NOTIFICATION[n.type] || []).includes(state.screen)) void loadScreen();
    if (['TOPUP_SUCCEEDED', 'CHARGING_COMPLETED', 'LOW_BALANCE'].includes(n.type)) void refreshWallet().then(() => { if (state.screen === 'wallet') render(); }).catch(() => null);
    if (n.type === 'STATION_ERROR') scheduleStationsRefresh();
    return;
  }
  if (event.type === 'member_session_updated' && (event.event === 'STARTED' || event.event === 'STOPPED')) scheduleStationsRefresh();
  if (event.type === 'member_session_updated' && isMember() && event.user_id === state.user.user_id) {
    if (event.event === 'STOPPED') {
      state.session = null;
      void refreshWallet();
      if (['charge', 'map', 'stationDetail'].includes(state.screen)) go('result', { session: event.session_id });
      return;
    }
    if (event.event === 'STARTED' || !state.session) {
      void refreshChargingState().then(() => { if (state.screen === 'charge') void loadScreen(); else if (state.screen === 'map') render(); });
      if (event.event === 'STARTED') toast(`Phiên sạc đã bắt đầu tại ${event.station_id}`);
      return;
    }
    Object.assign(state.session, {
      cost_vnd: event.cost_vnd ?? state.session.cost_vnd,
      energy_kwh: event.energy_kwh ?? state.session.energy_kwh,
      duration_seconds: event.duration_seconds ?? state.session.duration_seconds,
      remaining_budget_vnd: event.remaining_budget_vnd ?? state.session.remaining_budget_vnd,
      power_kw: event.power_kw ?? state.session.power_kw
    });
    if (state.screen === 'charge') render();
    return;
  }
  if (event.type === 'support_ticket_updated') {
    if (['techRequests', 'support'].includes(state.screen)) void loadScreen();
    if (['ticketDetail', 'supportDetail'].includes(state.screen) && state.params.ticket === event.ticket_id) void loadScreen();
    return;
  }
  if (['station_updated', 'station_status', 'station_error'].includes(event.type)) {
    scheduleStationsRefresh();
    const showsStation = state.params.station === event.station_id || state.data.ticket?.station_id === event.station_id;
    if (state.screen === 'techStations' || state.screen === 'techRequests' || (['techStationDetail', 'ticketDetail'].includes(state.screen) && showsStation)) {
      if (!document.activeElement?.matches('textarea, input')) void loadScreen();
    }
  }
}

let stationsTimer = null;
function scheduleStationsRefresh() {
  window.clearTimeout(stationsTimer);
  stationsTimer = window.setTimeout(refreshStations, 1500);
}

// ---------------------------------------------------------------------------
// Session lifecycle
// ---------------------------------------------------------------------------

async function startApp(user) {
  state.user = user;
  try { localStorage.setItem(cfg.userStorageKey, JSON.stringify(user)); } catch { /* private mode */ }
  await Promise.all([refreshStations(), refreshFavorites(), refreshUnread(), refreshWallet().catch(() => null), refreshChargingState().catch(() => null)]);
  connectRealtime();
  startPolling();
  if ('Notification' in window && window.Notification.permission === 'default') void window.Notification.requestPermission().catch(() => null);
  go(state.session ? 'charge' : homeScreen(), {}, { reset: true });
}

function signOut(message) {
  tokenStore.set(null);
  try { localStorage.removeItem(cfg.userStorageKey); } catch { /* private mode */ }
  state.user = null;
  state.session = null;
  state.wallet = null;
  state.unread = 0;
  state.favorites = new Set();
  state.ws?.close();
  state.ws = null;
  state.stack = [];
  state.screen = 'login';
  state.params = {};
  render();
  if (message) toast(message);
}

async function acceptLogin(result) {
  if (result.user.role === 'ADMIN') {
    toast('Tài khoản quản trị dùng web admin, không dùng ứng dụng di động.');
    return;
  }
  tokenStore.set(result.access_token);
  await startApp(result.user);
}

// ---------------------------------------------------------------------------
// Forms
// ---------------------------------------------------------------------------

const FORMS = {
  async login(f) {
    await acceptLogin(await api('/auth/login', { method: 'POST', body: { email: f.email.trim(), password: f.password } }));
  },
  async forgotPassword(f) {
    const issued = await api('/auth/forgot-password', { method: 'POST', body: { email: f.email.trim() } });
    state.data.issued = { ...issued, email: f.email.trim() };
    render();
  },
  async resetPassword(f) {
    if (f.new_password !== f.confirm) throw new Error('Mật khẩu nhập lại không khớp.');
    const result = await api('/auth/reset-password', { method: 'POST', body: { email: f.email.trim(), temporary_password: f.temporary_password.trim(), new_password: f.new_password } });
    toast('Đã đặt mật khẩu mới');
    await acceptLogin(result);
  },
  async changePassword(f) {
    if (f.new_password !== f.confirm) throw new Error('Mật khẩu nhập lại không khớp.');
    const result = await api('/auth/change-password', { method: 'POST', body: { current_password: f.current_password, new_password: f.new_password } });
    tokenStore.set(result.access_token);
    state.ws?.close();
    state.ws = null;
    connectRealtime();
    toast('Đã đổi mật khẩu, các thiết bị khác đã đăng xuất');
    back();
  },
  async editProfile(f) {
    state.user = await api('/auth/me', { method: 'PATCH', body: { name: f.name.trim(), phone: f.phone.trim() || null } });
    toast('Đã lưu thông tin');
    back();
  },
  async supportNew(f) {
    const ticket = await api('/member/support-tickets', { method: 'POST', body: { station_id: f.station_id, category: f.category, description: f.description.trim() } });
    toast('Đã gửi yêu cầu, kỹ thuật viên đã được thông báo');
    go('supportDetail', { ticket: ticket.ticket_id }, { replace: true });
  },
  async resolveTicket(f) {
    await api(`/technician/support-tickets/${encodeURIComponent(state.params.ticket)}/resolve`, { method: 'POST', body: { resolution_note: f.resolution_note.trim() } });
    toast('Đã hoàn tất yêu cầu, member đã được thông báo');
    scheduleStationsRefresh();
    void loadScreen();
  }
};

appRoot.addEventListener('submit', async (event) => {
  const form = event.target.closest('form[data-form]');
  if (!form) return;
  event.preventDefault();
  const button = form.querySelector('button:not([type="button"])');
  if (button) button.disabled = true;
  try {
    await FORMS[form.dataset.form](Object.fromEntries(new FormData(form)));
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Clicks
// ---------------------------------------------------------------------------

async function run(action, fn) {
  try { await fn(); } catch (e) { toast(e.message, 'error'); }
}

const ACTIONS = {
  back: () => back(),
  reload: () => loadScreen(),
  logout: () => run('logout', async () => { await api('/auth/logout', { method: 'POST' }).catch(() => null); signOut(); }),
  mapFilter: (el) => { state.mapFilter = el.dataset.filter; state.mapSelected = null; render(); },
  directions: (el) => openDirections(stationById(el.dataset.station) || { latitude: Number(el.dataset.lat), longitude: Number(el.dataset.lng) }),
  toggleFavorite: (el) => run('fav', async () => {
    const id = el.dataset.station;
    const fav = state.favorites.has(id);
    await api(`/member/favorite-stations/${encodeURIComponent(id)}`, { method: fav ? 'DELETE' : 'PUT' });
    fav ? state.favorites.delete(id) : state.favorites.add(id);
    toast(fav ? 'Đã bỏ khỏi trạm yêu thích' : 'Đã thêm vào trạm yêu thích');
    render();
  }),
  topupAmount: (el) => { state.data.amount = Number(el.dataset.amount); render(); },
  topup: (el) => run('topup', async () => {
    const amount = Number(el.dataset.amount);
    await api('/member/topup', { method: 'POST', body: { amount_vnd: amount } });
    await refreshWallet();
    toast(`Đã nạp ${money(amount)} vào ví`);
    back();
  }),
  txFilter: (el) => { state.data.filter = el.dataset.filter; render(); },
  moreSessions: () => run('more', async () => {
    const items = await api('/member/sessions', { query: { limit: 20, offset: state.data.sessions.length } });
    state.data.sessions = [...state.data.sessions, ...items];
    state.data.more = items.length === 20;
    render();
  }),
  cardAction: (el) => run('card', async () => {
    const op = el.dataset.op;
    const question = { lock: 'Tạm khoá thẻ? Thẻ không quẹt sạc được cho tới khi bạn mở khoá.', 'report-lost': 'Báo mất thẻ? Thẻ bị vô hiệu vĩnh viễn, bạn cần tới quầy để được cấp thẻ mới.', unlock: null }[op];
    if (question && !window.confirm(question)) return;
    await api(`/${el.dataset.base}/rfid-cards/${encodeURIComponent(el.dataset.card)}/${op}`, { method: 'POST' });
    toast({ lock: 'Đã tạm khoá thẻ', unlock: 'Đã mở khoá thẻ', 'report-lost': 'Đã báo mất thẻ' }[op]);
    await loadScreen();
  }),
  cancelTicket: () => run('cancelTicket', async () => {
    if (!window.confirm('Huỷ yêu cầu hỗ trợ này?')) return;
    await api(`/member/support-tickets/${encodeURIComponent(state.params.ticket)}/cancel`, { method: 'POST' });
    toast('Đã huỷ yêu cầu');
    await loadScreen();
  }),
  acceptTicket: () => run('accept', async () => {
    await api(`/technician/support-tickets/${encodeURIComponent(state.params.ticket)}/accept`, { method: 'POST' });
    toast('Đã nhận yêu cầu, member đã được thông báo');
    await loadScreen();
  }),
  techView: (el) => { state.data.view = el.dataset.view; state.data.tickets = undefined; render(); void loadScreen(); },
  historyTab: (el) => { state.data.tab = el.dataset.tabKey; render(); void loadScreen(); },
  faq: (el) => { const i = Number(el.dataset.index); state.data.open = state.data.open === i ? -1 : i; render(); },
  copyTemp: () => {
    navigator.clipboard?.writeText(state.data.issued.temporary_password).then(() => toast('Đã sao chép mật khẩu tạm'), () => toast('Không sao chép được, hãy ghi lại'));
  },
  readAll: () => run('readAll', async () => {
    await api('/notifications/read-all', { method: 'POST' });
    state.unread = 0;
    await loadScreen();
  }),
  moreNotifications: () => run('moreN', async () => {
    const page = state.data.notifications;
    const next = await api('/notifications', { query: { limit: 30, offset: page.items.length } });
    state.data.notifications = { ...next, items: [...page.items, ...next.items] };
    render();
  }),
  openNotification: (el) => run('openN', async () => {
    const n = state.data.notifications.items.find((item) => item.notification_id === el.dataset.id);
    if (!n.read_at) {
      await api(`/notifications/${encodeURIComponent(n.notification_id)}/read`, { method: 'POST' });
      n.read_at = new Date().toISOString();
      state.unread = Math.max(0, state.unread - 1);
    }
    const d = n.data || {};
    if (d.ticket_id) return go(isTechnician() ? 'ticketDetail' : 'supportDetail', { ticket: d.ticket_id });
    if (n.type === 'STATION_ERROR' && d.station_id) return go('techStationDetail', { station: d.station_id });
    if (d.session_id) return go('sessionDetail', { session: d.session_id });
    if (d.card_id) return go('cards');
    if (d.transaction_id) return go('transactionDetail', { tx: d.transaction_id });
    if (n.type === 'LOW_BALANCE') return go('topup');
    render();
  })
};

appRoot.addEventListener('click', (event) => {
  const tabButton = event.target.closest('[data-tab]');
  if (tabButton) { state.mapSelected = null; go(tabButton.dataset.tab, {}, { reset: true }); return; }
  const link = event.target.closest('[data-go]');
  if (link) {
    const { go: screen, ...params } = link.dataset;
    if (screen === 'stations' || screen === 'map') state.mapSelected = null;
    go(screen, params);
    return;
  }
  const target = event.target.closest('[data-action]');
  if (target && ACTIONS[target.dataset.action]) ACTIONS[target.dataset.action](target);
});

appRoot.addEventListener('input', (event) => {
  const key = event.target.dataset.input;
  if (key === 'query') {
    state.query = event.target.value;
    if (state.screen === 'stations') {
      const list = document.querySelector('.station-list');
      const shown = state.stations.filter(stationMatches);
      list.innerHTML = shown.length ? shown.map(stationRow).join('') : emptyBlock('search', 'Không có trạm phù hợp');
      refreshIcons();
    }
  }
});

appRoot.addEventListener('change', (event) => {
  if (event.target.dataset.input === 'query' && state.screen === 'map') render();
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

/** The app was in the background (WebSocket may have dropped): reload what is on screen. */
let lastCatchUp = 0;
function catchUp() {
  if (document.hidden || !state.user || Date.now() - lastCatchUp < 3000) return;
  lastCatchUp = Date.now();
  void refreshStations();
  void refreshUnread();
  if (isMember()) void refreshChargingState().catch(() => null);
  if (!state.ws) connectRealtime();
  if (!document.activeElement?.matches('textarea, input, select')) void loadScreen();
}

async function boot() {
  render();
  document.addEventListener('visibilitychange', catchUp);
  window.addEventListener('focus', catchUp);
  void refreshStations();
  window.setInterval(refreshStations, cfg.stationsRefreshMs);
  window.setInterval(updateCountdowns, 1000);
  watchLocation();
  if (!tokenStore.get()) return;
  try {
    await startApp(await api('/auth/me'));
  } catch {
    signOut();
  }
}

void boot();
