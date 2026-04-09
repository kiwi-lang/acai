import type { AgentEvent, AgentMessage, AgentStatus, Project, Task, Worktree } from './types';

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

// Conversation (async — returns task_id, response streamed via WS)
export async function converse(message: string, project = '_default'): Promise<string> {
    const data = await request<{ task_id: string }>('/converse', {
        method: 'POST',
        body: JSON.stringify({ message, project }),
    });
    return data.task_id;
}

export async function getHistory(project = '_default'): Promise<AgentMessage[]> {
    const data = await request<{ messages: AgentMessage[] }>(`/history?project=${encodeURIComponent(project)}`);
    return data.messages;
}

export async function clearHistory(project = '_default'): Promise<void> {
    await request(`/history?project=${encodeURIComponent(project)}`, { method: 'DELETE' });
}

// Tasks
export async function listTasks(status?: string): Promise<Task[]> {
    const qs = status ? `?status=${status}` : '';
    return request<Task[]>(`/tasks${qs}`);
}

export async function getTask(id: string): Promise<Task> {
    return request<Task>(`/tasks/${id}`);
}

export async function createTask(title: string, description = '', priority = 0): Promise<Task> {
    return request<Task>('/tasks', {
        method: 'POST',
        body: JSON.stringify({ title, description, priority }),
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
