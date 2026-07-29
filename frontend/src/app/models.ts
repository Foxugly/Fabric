export type AgentStatus =
  | 'offline'
  | 'connecting'
  | 'online'
  | 'busy'
  | 'error'
  | 'disabled';

export type CommandStatus =
  | 'pending'
  | 'dispatched'
  | 'running'
  | 'waiting_user_action'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'timed_out';

export interface Agent {
  id: string;
  name: string;
  description: string;
  status: AgentStatus;
  version: string;
  operating_system: string;
  last_connected_at: string | null;
  last_activity_at: string | null;
  capabilities: string[];
  created_at: string;
  updated_at: string;
}

export interface Conversation {
  id: string;
  provider: string;
  external_id: string;
  title: string;
  url: string;
  agent: string;
  created_at: string;
  updated_at: string;
  last_accessed_at: string;
}

export interface Message {
  id: string;
  conversation: string;
  command_id: string | null;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed';
  external_id: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface CommandEvent {
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export type PermissionDecision = 'pending' | 'allowed' | 'denied' | 'expired';

export interface PermissionRequest {
  id: string;
  command: string;
  request_id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_use_id: string;
  decision: PermissionDecision;
  decision_message: string;
  created_at: string;
  decided_at: string | null;
}

export interface Command {
  id: string;
  agent: string;
  provider: string;
  action: string;
  payload: Record<string, unknown>;
  status: CommandStatus;
  result: Record<string, unknown>;
  error: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  timeout_seconds: number;
  correlation_id: string;
  events: CommandEvent[];
  permission_requests: PermissionRequest[];
}

export interface CreateCommandRequest {
  agent_id: string;
  provider: string;
  action: string;
  payload: Record<string, unknown>;
  timeout_seconds?: number;
}

export interface CreateConversationRequest {
  agent_id: string;
  provider: string;
  title?: string;
}

export interface CreateMessageRequest {
  content: string;
}

export interface CreateMessageResponse {
  message: Message;
  command_id: string;
  status: CommandStatus;
}

export interface FabricEvent {
  type: string;
  payload: Record<string, unknown>;
}

export interface FabricUser {
  id: number;
  username: string;
  is_staff: boolean;
  is_superuser: boolean;
}

export interface AuthSession {
  token: string;
  user: FabricUser;
}

export interface CommandOption {
  label: string;
  provider: string;
  action: string;
  payloadTemplate: Record<string, unknown>;
  timeoutSeconds: number;
}
