import type { AgentDef, AgentEvent, AgentMessage, AgentStatus, ConversationMeta, Project, Provider, Task, Worktree } from './types';

const API_BASE = '/api/agent';

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${API_BASE}${endpoint}`;
    const config: RequestInit = {
        headers: { 'Content-Type': 'application/json', ...options.headers as Record<string, string> },
        ...options,
    };

    const response = await fetch(url, config);
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${response.status}`);
    }
    return response.json();
}

// Conversations CRUD
export async function listConversations(): Promise<ConversationMeta[]> {
    return request<ConversationMeta[]>('/conversations');
}

export async function createConversation(title = '', project = ''): Promise<ConversationMeta> {
    return request<ConversationMeta>('/conversations', {
        method: 'POST',
        body: JSON.stringify({ title, project }),
    });
}

export async function getConversation(id: string): Promise<ConversationMeta> {
    return request<ConversationMeta>(`/conversations/${id}`);
}

export async function deleteConversation(id: string): Promise<void> {
    await request(`/conversations/${id}`, { method: 'DELETE' });
}

// SSE stream from a POST response (EventSource only supports GET)
export class SSEStream {
    private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    private decoder = new TextDecoder();
    private listeners: Record<string, Array<(e: MessageEvent) => void>> = {};
    private _closed = false;

    onerror: ((reason?: string) => void) | null = null;

    constructor(response: Response) {
        if (!response.body) throw new Error('Response has no body');
        this.reader = response.body.getReader();
        this._pump();
    }

    addEventListener(event: string, cb: (e: MessageEvent) => void) {
        (this.listeners[event] ||= []).push(cb);
    }

    close() {
        this._closed = true;
        this.onerror = null;
        this.listeners = {};
        const r = this.reader;
        this.reader = null;
        r?.cancel().catch(() => {});
    }

    private async _pump() {
        let buffer = '';
        try {
            while (!this._closed && this.reader) {
                const { done, value } = await this.reader.read();
                if (done || this._closed) break;

                buffer += this.decoder.decode(value, { stream: true });
                const frames = buffer.split('\n\n');
                buffer = frames.pop()!;

                for (const frame of frames) {
                    if (!frame.trim() || this._closed) continue;
                    this._dispatch(frame);
                }
            }
        } catch (err) {
            if (this._closed) return;
            const reason = err instanceof Error ? err.message : 'Connection lost';
            this._dispatch(`event: error\ndata: ${JSON.stringify({ message: reason })}`);
            this.onerror?.(reason);
            return;
        }
        if (!this._closed) this.onerror?.('Stream ended unexpectedly');
    }

    private _dispatch(frame: string) {
        if (this._closed) return;
        let eventType = 'message';
        const dataLines: string[] = [];

        for (const line of frame.split('\n')) {
            if (line.startsWith('event: ')) eventType = line.slice(7).trim();
            else if (line.startsWith('data: ')) dataLines.push(line.slice(6));
            else if (line.startsWith('data:')) dataLines.push(line.slice(5));
        }

        const data = dataLines.join('\n');
        const me = new MessageEvent(eventType, { data });
        for (const cb of this.listeners[eventType] || []) cb(me);
    }
}

// Converse — returns an SSE stream; conversation id arrives as the first "meta" event
export async function converse(
    message: string,
    conversation = '',
    project = '',
    parent_task = '',
    provider = '',
    agent = '',
    enable_thinking?: boolean,
): Promise<{ conversation: string; stream: SSEStream }> {
    const body: Record<string, unknown> = { message, conversation, project, parent_task, provider, agent };
    if (enable_thinking !== undefined) body.enable_thinking = enable_thinking;

    const response = await fetch(`${API_BASE}/converse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });

    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || err.message || `HTTP ${response.status}`);
    }

    const convId = response.headers.get('X-Conversation') || conversation;
    return { conversation: convId, stream: new SSEStream(response) };
}

// Think-then-converse (emulated reasoning for models without native thinking)
export async function thinkConverse(
    message: string,
    conversation = '',
    project = '',
    parent_task = '',
    provider = '',
    agent = '',
): Promise<{ conversation: string; stream: SSEStream }> {
    const body: Record<string, unknown> = { message, conversation, project, parent_task, provider, agent };

    const response = await fetch(`${API_BASE}/think/converse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });

    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || err.message || `HTTP ${response.status}`);
    }

    const convId = response.headers.get('X-Conversation') || conversation;
    return { conversation: convId, stream: new SSEStream(response) };
}

export interface HistoryResponse {
    messages: AgentMessage[];
    streaming: { task_id: string; partial: string } | null;
}

export async function getHistory(conversation: string): Promise<HistoryResponse> {
    return request<HistoryResponse>(`/history?conversation=${encodeURIComponent(conversation)}`);
}

export async function clearHistory(conversation: string): Promise<void> {
    await request(`/history?conversation=${encodeURIComponent(conversation)}`, { method: 'DELETE' });
}

// Tasks
export interface ListTasksParams {
    status?: string;
    project?: string;
    rootOnly?: boolean;
}

export async function listTasks(params: ListTasksParams = {}): Promise<Task[]> {
    const parts: string[] = [];
    if (params.status) parts.push(`status=${encodeURIComponent(params.status)}`);
    if (params.project) parts.push(`project=${encodeURIComponent(params.project)}`);
    if (params.rootOnly) parts.push('root_only=true');
    const qs = parts.length ? `?${parts.join('&')}` : '';
    return request<Task[]>(`/tasks${qs}`);
}

export async function getTask(id: string): Promise<Task> {
    return request<Task>(`/tasks/${id}`);
}

export async function getTaskTree(id: string): Promise<Task[]> {
    return request<Task[]>(`/tasks/${id}/tree`);
}

export async function createTask(
    title: string,
    description = '',
    priority = 0,
    project = '',
    parent_task = '',
): Promise<Task> {
    return request<Task>('/tasks', {
        method: 'POST',
        body: JSON.stringify({ title, description, priority, project, parent_task, kind: 'task' }),
    });
}

export async function updateTask(id: string, fields: Partial<Task>): Promise<Task> {
    return request<Task>(`/tasks/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(fields),
    });
}

// Specs
export async function listSpecs(): Promise<string[]> {
    return request<string[]>('/specs');
}

export async function getSpec(name: string): Promise<{ name: string; content: string }> {
    return request(`/specs/${name}`);
}

// Worktrees
export async function listWorktrees(): Promise<Worktree[]> {
    return request<Worktree[]>('/worktrees');
}

// Events
export async function listEvents(limit = 50): Promise<AgentEvent[]> {
    return request<AgentEvent[]>(`/events?limit=${limit}`);
}

// Status
export async function getStatus(): Promise<AgentStatus> {
    return request<AgentStatus>('/status');
}

// Projects
export async function listProjects(): Promise<Project[]> {
    return request<Project[]>('/projects');
}

export async function createProject(data: Partial<Project>): Promise<Project> {
    return request<Project>('/projects', {
        method: 'POST',
        body: JSON.stringify(data),
    });
}

export async function getProject(id: string): Promise<Project> {
    return request<Project>(`/projects/${id}`);
}

export async function updateProject(name: string, data: Partial<Omit<Project, 'id' | 'name' | 'source' | 'created_at'>>): Promise<Project> {
    return request<Project>(`/projects/${name}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
    });
}

export async function deleteProject(id: string): Promise<void> {
    await request(`/projects/${id}`, { method: 'DELETE' });
}

// Conversations update
export async function updateConversation(
    id: string,
    fields: { title?: string; description?: string; provider?: string; agent?: string; tags?: string[] },
): Promise<ConversationMeta> {
    return request<ConversationMeta>(`/conversations/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(fields),
    });
}

// Providers
export async function listProviders(): Promise<Provider[]> {
    return request<Provider[]>('/providers');
}

export async function createProvider(data: Partial<Provider>): Promise<Provider> {
    return request<Provider>('/providers', {
        method: 'POST',
        body: JSON.stringify(data),
    });
}

export async function getProvider(name: string): Promise<Provider> {
    return request<Provider>(`/providers/${name}`);
}

export async function updateProvider(name: string, data: Partial<Provider>): Promise<Provider> {
    return request<Provider>(`/providers/${name}`, {
        method: 'PUT',
        body: JSON.stringify(data),
    });
}

export async function deleteProvider(name: string): Promise<void> {
    await request(`/providers/${name}`, { method: 'DELETE' });
}

export async function activateProvider(name: string): Promise<Provider> {
    return request<Provider>(`/providers/${name}/activate`, { method: 'POST' });
}

// Agents
export async function listAgents(): Promise<AgentDef[]> {
    return request<AgentDef[]>('/agents');
}

export async function createAgent(data: Partial<AgentDef>): Promise<AgentDef> {
    return request<AgentDef>('/agents', {
        method: 'POST',
        body: JSON.stringify(data),
    });
}

export async function getAgent(name: string): Promise<AgentDef> {
    return request<AgentDef>(`/agents/${name}`);
}

export async function updateAgent(name: string, data: Partial<AgentDef>): Promise<AgentDef> {
    return request<AgentDef>(`/agents/${name}`, {
        method: 'PUT',
        body: JSON.stringify(data),
    });
}

export async function deleteAgent(name: string): Promise<void> {
    await request(`/agents/${name}`, { method: 'DELETE' });
}

export async function resetAgent(name: string): Promise<AgentDef> {
    return request<AgentDef>(`/agents/${name}/reset`, { method: 'POST' });
}

export async function getAgentTemplate(name: string): Promise<{ name: string; content: string }> {
    return request(`/agents/${name}/template`);
}

export async function updateAgentTemplate(name: string, content: string): Promise<{ name: string; content: string }> {
    return request(`/agents/${name}/template`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
    });
}

// Tool namespaces
export interface ToolNamespace {
    namespace: string;
    tools: string[];
}

export async function listToolNamespaces(): Promise<ToolNamespace[]> {
    return request<ToolNamespace[]>('/tools/namespaces');
}

// Workflow types

export interface WorkflowSummary {
    id: string;
    name: string;
    description: string;
    node_count: number;
    edge_count: number;
    builtin?: boolean;
}

export interface WorkflowSpec {
    id: string;
    name: string;
    description: string;
    builtin?: boolean;
    nodes: Array<{
        id: string;
        type: string;
        position: { x: number; y: number };
        data: Record<string, unknown>;
    }>;
    edges: Array<{
        id: string;
        source: string;
        target: string;
        sourceHandle: string;
        targetHandle: string;
        type?: string;
    }>;
}

// Workflows CRUD
export async function listWorkflows(): Promise<WorkflowSummary[]> {
    return request<WorkflowSummary[]>('/workflows');
}

export async function getWorkflow(id: string): Promise<WorkflowSpec> {
    return request<WorkflowSpec>(`/workflows/${id}`);
}

export async function saveWorkflow(spec: WorkflowSpec): Promise<WorkflowSpec> {
    return request<WorkflowSpec>('/workflows', {
        method: 'POST',
        body: JSON.stringify(spec),
    });
}

export async function updateWorkflow(id: string, spec: WorkflowSpec): Promise<WorkflowSpec> {
    return request<WorkflowSpec>(`/workflows/${id}`, {
        method: 'PUT',
        body: JSON.stringify(spec),
    });
}

export async function deleteWorkflow(id: string): Promise<void> {
    await request(`/workflows/${id}`, { method: 'DELETE' });
}

export async function runWorkflow(id: string, message = '', conversation = ''): Promise<Response> {
    const response = await fetch(`${API_BASE}/workflows/${id}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, conversation }),
    });
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${response.status}`);
    }
    return response;
}

export async function saveBuiltinWorkflow(id: string, spec: WorkflowSpec): Promise<WorkflowSpec> {
    return request<WorkflowSpec>(`/workflows/builtin/${id}`, {
        method: 'PUT',
        body: JSON.stringify(spec),
    });
}

// Uber conversation routing — returns SSE stream with route + done events
export async function uberConverse(
    message: string,
    currentConversation = '',
    agent = '',
): Promise<{ stream: SSEStream }> {
    const response = await fetch(`${API_BASE}/uber/converse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message,
            current_conversation: currentConversation,
            agent,
        }),
    });

    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || err.message || `HTTP ${response.status}`);
    }

    return { stream: new SSEStream(response) };
}

// Conversation inflight check
export async function checkInflight(convId: string): Promise<{ inflight: boolean; task_id?: string; status?: string }> {
    return request(`/conversations/${convId}/inflight`);
}

// Context stats
export async function getContextStats(convId: string): Promise<{ estimated_tokens: number; max_context: number }> {
    return request(`/conversations/${convId}/context-stats`);
}
