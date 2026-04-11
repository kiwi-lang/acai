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

// Converse (async — returns task_id + conversation, response streamed via WS)
export async function converse(
    message: string,
    conversation = '',
    project = '',
    parent_task = '',
    provider = '',
    agent = '',
): Promise<{ task_id: string; conversation: string }> {
    return request<{ task_id: string; conversation: string }>('/converse', {
        method: 'POST',
        body: JSON.stringify({ message, conversation, project, parent_task, provider, agent }),
    });
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

export async function deleteProject(id: string): Promise<void> {
    await request(`/projects/${id}`, { method: 'DELETE' });
}

// Conversations update
export async function updateConversation(
    id: string,
    fields: { title?: string; provider?: string; agent?: string },
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

// Conversation inflight check
export async function checkInflight(convId: string): Promise<{ inflight: boolean; task_id?: string; status?: string }> {
    return request(`/conversations/${convId}/inflight`);
}

// Context stats
export async function getContextStats(convId: string): Promise<{ estimated_tokens: number; max_context: number }> {
    return request(`/conversations/${convId}/context-stats`);
}
