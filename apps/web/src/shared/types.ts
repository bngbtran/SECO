/** Shapes returned by /api/v1/admin (apps/api/app/modules/admin/schemas.py). */

export type Decimal = string;

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type Role = "USER" | "ADMIN" | "TECHNICIAN";
export type Availability = "AVAILABLE" | "CHARGING" | "WAITING_PAYMENT" | "ERROR" | "MAINTENANCE" | "OFFLINE";
export type SessionStatus = "ACTIVE" | "WAITING_PAYMENT" | "COMPLETED" | "ERROR";

export interface Me {
  user_id: string;
  name: string;
  email: string;
  role: Role;
  balance_vnd: number;
  is_active: boolean;
  phone?: string | null;
}

export interface LoginResult {
  access_token: string;
  user: Me;
}

export interface TemporaryPassword {
  temporary_password: string;
  expires_at: string;
}

export interface AdminUser {
  user_id: string;
  name: string;
  email: string;
  phone: string | null;
  role: Role;
  balance_vnd: number;
  is_active: boolean;
  card_count: number;
  created_at: string;
  updated_at: string;
}

export interface AdminCard {
  card_id: string;
  uid: string | null;
  user_id: string | null;
  user_name: string | null;
  label: string | null;
  is_active: boolean;
  pending: boolean;
  status: "ACTIVE" | "INACTIVE" | "PENDING" | "UNASSIGNED";
  /** Why an INACTIVE card is blocked: LOCKED (desk / owner lock) or LOST (final). */
  block_reason?: "LOCKED" | "LOST" | null;
  blocked_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdminVehicle {
  vehicle_id: string;
  plate_number: string;
  brand: string;
  model: string;
  battery_kwh: number | null;
  is_active: boolean;
}

export interface AdminSession {
  session_id: string;
  station_id: string;
  station_name: string | null;
  type: "MEMBER" | "GUEST";
  user_id: string | null;
  user_name: string | null;
  status: SessionStatus;
  max_budget_vnd: number | null;
  budget_mode: string | null;
  remaining_budget_vnd: number | null;
  energy_kwh: Decimal;
  power_kw: Decimal;
  price_per_kwh_vnd: number;
  cost_vnd: number;
  stop_reason: string | null;
  connector_locked: boolean;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number;
}

export interface AdminTransaction {
  transaction_id: string;
  type: "CHARGING" | "GUEST_PAYMENT" | "TOPUP";
  user_id: string | null;
  user_name: string | null;
  session_id: string | null;
  payment_id: string | null;
  payment_method: string | null;
  amount_vnd: number;
  balance_before_vnd: number | null;
  balance_after_vnd: number | null;
  status: "COMPLETED" | "FAILED";
  created_at: string;
}

export interface AdminPayment {
  payment_id: string;
  session_id: string;
  station_id: string | null;
  station_name: string | null;
  amount_vnd: number;
  status: "PENDING" | "PAID" | "EXPIRED" | "CANCELLED";
  provider: string;
  qr_payload: string;
  created_at: string;
  expires_at: string;
  paid_at: string | null;
}

export interface AdminWalletHold {
  wallet_hold_id: string;
  user_id: string;
  user_name: string | null;
  session_id: string;
  amount_vnd: number;
  status: string;
  created_at: string;
  released_at: string | null;
}

export interface AdminSessionDetail extends AdminSession {
  transactions: AdminTransaction[];
  payments: AdminPayment[];
}

export interface AdminUserDetail extends AdminUser {
  cards: AdminCard[];
  vehicles: AdminVehicle[];
  open_session: AdminSession | null;
  recent_sessions: AdminSession[];
  recent_transactions: AdminTransaction[];
}

export interface Telemetry {
  voltage_v?: number;
  current_a?: number;
  power_kw?: number;
  temperature_c?: number;
  [key: string]: unknown;
}

export interface AdminStation {
  station_id: string;
  name: string;
  address: string | null;
  status: "READY" | "CHARGING" | "ERROR" | "MAINTENANCE";
  availability: Availability;
  is_online: boolean;
  is_simulated: boolean;
  connector_lock: "UNLOCKED" | "LOCKED" | "UNKNOWN";
  current_session_id: string | null;
  last_heartbeat_at: string | null;
  latitude: number | null;
  longitude: number | null;
  latest_telemetry: Telemetry | null;
  created_at: string;
  updated_at: string;
}

export interface AdminLiveStation extends AdminStation {
  current_session: AdminSession | null;
}

export interface AdminMaintenance {
  maintenance_id: string;
  station_id: string;
  station_name: string | null;
  technician_id: string;
  technician_name: string | null;
  error_id: string | null;
  status: "IN_PROGRESS" | "COMPLETED" | "CANCELLED";
  description: string;
  started_at: string;
  completed_at: string | null;
}

export interface AdminErrorLog {
  error_id: string;
  station_id: string;
  station_name: string | null;
  session_id: string | null;
  error_type: string;
  severity: "WARNING" | "ERROR" | "CRITICAL";
  message: string;
  details: Record<string, unknown> | null;
  occurred_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  resolved_by_name: string | null;
}

export interface AdminStationDetail extends AdminStation {
  current_session: AdminSession | null;
  recent_sessions: AdminSession[];
  open_errors: AdminErrorLog[];
  maintenance: AdminMaintenance[];
}

export type StationCommand = "STOP" | "MAINTENANCE" | "READY" | "RESET" | "LOCK" | "UNLOCK" | "PING";

export interface StationCommandResult {
  station_id: string;
  command: StationCommand;
  request_id: string;
  published: boolean;
  stopped_session: AdminSession | null;
  station: AdminStation;
}

export interface DashboardDay {
  date: string;
  sessions: number;
  energy_kwh: Decimal;
  revenue_vnd: number;
}

export interface AdminDashboard {
  generated_at: string;
  stations_total: number;
  stations_by_availability: Record<Availability, number>;
  sessions_active: number;
  sessions_waiting_payment: number;
  sessions_completed_today: number;
  energy_today_kwh: Decimal;
  revenue_today_vnd: number;
  members_total: number;
  members_active: number;
  open_errors: number;
  maintenance_in_progress: number;
  price_per_kwh_vnd: number;
  last_7_days: DashboardDay[];
  live_sessions: AdminSession[];
  recent_errors: AdminErrorLog[];
}

/** Events pushed on /ws (member/guest sessions, stations, errors, command acks). */
export interface RealtimeEvent {
  type: string;
  event?: string;
  station_id?: string;
  session_id?: string;
  status?: string;
  energy_kwh?: number | string;
  cost_vnd?: number;
  power_kw?: number;
  voltage_v?: number;
  current_a?: number;
  temperature_c?: number;
  stop_reason?: string | null;
  error_type?: string;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface AdminSupportTicket {
  ticket_id: string;
  station_id: string;
  station_name: string | null;
  category: string;
  category_label: string;
  description: string;
  status: "OPEN" | "IN_PROGRESS" | "RESOLVED" | "CANCELLED";
  session_id: string | null;
  user_id: string;
  user_name: string | null;
  user_phone: string | null;
  assigned_to: string | null;
  assigned_name: string | null;
  assigned_at: string | null;
  resolution_note: string | null;
  resolved_at: string | null;
  cancelled_at: string | null;
  created_at: string;
  updated_at: string;
  /** Station state when the member sent the report. */
  station_snapshot: {
    status?: string;
    availability?: string;
    is_online?: boolean;
    connector_lock?: string;
    last_heartbeat_at?: string | null;
    latest_telemetry?: Record<string, unknown> | null;
    open_errors?: { error_id: string; error_type: string; severity: string; message: string; occurred_at: string }[];
  };
}
