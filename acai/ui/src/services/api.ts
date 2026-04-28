import type { AgentDef, AgentEvent, AgentMessage, AgentStatus, ConversationMeta, Project, Provider, SystemConfig, Task, Worktree } from './types';

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
export async function listConversations(
    opts: { project?: string; task_id?: string } = {},
): Promise<ConversationMeta[]> {
    const params = new URLSearchParams();
    if (opts.project) params.set('project', opts.project);
    if (opts.task_id) params.set('task_id', opts.task_id);
    const qs = params.toString();
    return request<ConversationMeta[]>(`/conversations${qs ? `?${qs}` : ''}`);
}

export async function createConversation(
    title = '',
    project = '',
    task_id = '',
): Promise<ConversationMeta> {
    const body: Record<string, string> = { title, project };
    if (task_id) body.task_id = task_id;
    return request<ConversationMeta>('/conversations', {
        method: 'POST',
        body: JSON.stringify(body),
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

// Graph definitions
export interface GraphDef {
    kind: string;
    label: string;
    description: string;
}

export async function listGraphs(): Promise<GraphDef[]> {
    return request<GraphDef[]>('/graphs');
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
    graph?: string,
    ephemeral?: boolean,
    task_id?: string,
): Promise<{ conversation: string; stream: SSEStream }> {
    const body: Record<string, unknown> = { message, conversation, project, parent_task, provider, agent };
    if (task_id) body.task_id = task_id;
    if (enable_thinking !== undefined) body.enable_thinking = enable_thinking;
    if (graph) body.graph = graph;
    if (ephemeral) body.ephemeral = true;

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
    task_id?: string,
): Promise<{ conversation: string; stream: SSEStream }> {
    const body: Record<string, unknown> = { message, conversation, project, parent_task, provider, agent };
    if (task_id) body.task_id = task_id;

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

export interface ToolDefinition {
    type: string;
    function: {
        name: string;
        description: string;
        parameters: {
            type: string;
            properties: Record<string, { type: string; description?: string }>;
            required: string[];
        };
        permissions: string[];
    };
}

export async function listToolDefinitions(): Promise<ToolDefinition[]> {
    return request<ToolDefinition[]>('/workflows/tool-definitions');
}

// Node types (from server)

export interface PinDef {
    id: string;
    label: string;
    color: string;
    side: 'left' | 'right';
    kind: 'exec' | 'data';
    pin_type?: string;
    choices?: string[];
    dynamic_choices?: string;
    optional?: boolean;
}

export interface NodeTypeDef {
    type: string;
    label: string;
    accent: string;
    description: string;
    category: string;
    pins: PinDef[];
}

export async function getNodeTypes(): Promise<NodeTypeDef[]> {
    return request<NodeTypeDef[]>('/workflows/node-types');
}

export async function getAgentInputs(agentName: string): Promise<string[]> {
    const res = await request<{ agent: string; inputs: string[] }>(`/workflows/agent-inputs/${agentName}`);
    return res.inputs;
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

export async function runWorkflow(
    id: string,
    message = '',
    conversation = '',
    test = false,
    testConversation?: Array<{ role: string; content: string }>,
): Promise<Response> {
    const body: Record<string, unknown> = { message, conversation, test };
    if (testConversation && testConversation.length > 0) {
        body.test_conversation = testConversation;
    }
    const response = await fetch(`${API_BASE}/workflows/${id}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${response.status}`);
    }
    return response;
}

export interface Diagnostic {
    severity: 'error' | 'warning';
    code: string;
    node_id: string;
    message: string;
    edge_id?: string;
    source_node?: string;
    target_node?: string;
    source_pin?: string;
    target_pin?: string;
    source_type?: string;
    target_type?: string;
}

export interface ValidationError {
    edge_id: string;
    source_node: string;
    target_node: string;
    source_pin: string;
    target_pin: string;
    source_type: string;
    target_type: string;
    message: string;
}

export interface ValidationResult {
    diagnostics: Diagnostic[];
    errors: Diagnostic[];
    warnings: Diagnostic[];
    valid: boolean;
}

export async function validateWorkflow(id: string): Promise<ValidationResult> {
    return request<ValidationResult>(`/workflows/${id}/validate`, { method: 'POST' });
}

export async function validateWorkflowSpec(spec: WorkflowSpec): Promise<ValidationResult> {
    return request<ValidationResult>('/workflows/validate', {
        method: 'POST',
        body: JSON.stringify(spec),
    });
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

// Audit trail
export async function getAudit(auditId: string): Promise<any> {
    return request(`/audit/${auditId}`);
}

// System config
export async function getConfig(): Promise<SystemConfig> {
    return request<SystemConfig>('/config');
}

export async function updateConfig(patch: Record<string, any>): Promise<SystemConfig> {
    return request<SystemConfig>('/config', {
        method: 'PATCH',
        body: JSON.stringify(patch),
    });
}

// -- Version / Auto-update ------------------------------------------------

export interface VersionInfo {
    version: string;
    latest?: string;
    update_available?: boolean;
}

export async function getVersion(): Promise<VersionInfo> {
    const local = await request<VersionInfo>('/version');

    try {
        const pypi = await fetch(`https://pypi.org/pypi/acai-swarm/json?_t=${Date.now()}`, {
            cache: 'no-store',
            headers: { Accept: 'application/json' },
        });
        if (pypi.ok) {
            const data = await pypi.json();
            const latest = data?.info?.version as string | undefined;
            if (latest) {
                local.latest = latest;
                local.update_available = _versionNewer(latest, local.version);
            }
        }
    } catch { /* PyPI unreachable — keep whatever the backend returned */ }

    return local;
}

function _versionNewer(latest: string, current: string): boolean {
    const a = latest.split('.').map(Number);
    const b = current.split('.').map(Number);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
        const x = a[i] ?? 0;
        const y = b[i] ?? 0;
        if (x > y) return true;
        if (x < y) return false;
    }
    return false;
}

export async function triggerUpdate(): Promise<SSEStream> {
    const response = await fetch(`${API_BASE}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    });
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${response.status}`);
    }
    return new SSEStream(response);
}

// -- Git Backup -----------------------------------------------------------

export interface GitBackupStatus {
    initialized: boolean;
    remote: string | null;
    ssh_key_exists: boolean;
    ssh_public_key: string;
    recent_commits: string[];
    dirty: boolean;
    data_path: string;
    last_sync?: {
        commit: string | null;
        pushed: boolean;
        push_error: string | null;
        error: string | null;
        timestamp: string;
    };
}

export async function getGitBackupStatus(): Promise<GitBackupStatus> {
    return request<GitBackupStatus>('/git/status');
}

export async function generateGitKey(): Promise<{ public_key: string }> {
    return request<{ public_key: string }>('/git/generate-key', { method: 'POST' });
}

export async function getGitSshKey(): Promise<{ public_key: string }> {
    return request<{ public_key: string }>('/git/ssh-key');
}

export async function setupGitBackup(remote: string): Promise<any> {
    return request('/git/setup', {
        method: 'POST',
        body: JSON.stringify({ remote }),
    });
}

export async function triggerGitSync(): Promise<any> {
    return request('/git/sync', { method: 'POST' });
}

export async function testGitConnection(): Promise<{ connected: boolean; output: string }> {
    return request('/git/test', { method: 'POST' });
}

// Skills
export interface SkillSummary {
    qualified_name: string;
    namespace: string;
    name: string;
    description: string;
    path: string;
}

export interface SkillDetail {
    qualified_name: string;
    namespace: string;
    name: string;
    definition: {
        name: string;
        description: string;
        parameters?: {
            type: string;
            properties: Record<string, { type: string; description?: string }>;
            required?: string[];
        };
    };
    code: string;
    readme: string;
}

export async function listSkills(): Promise<SkillSummary[]> {
    return request<SkillSummary[]>('/skills');
}

export async function getSkill(namespace: string, name: string): Promise<SkillDetail> {
    return request<SkillDetail>(`/skills/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}`);
}

export async function createSkill(data: {
    namespace: string;
    name: string;
    description: string;
    parameters?: Record<string, unknown>;
    code?: string;
    readme?: string;
}): Promise<{ created: boolean; qualified_name: string; path: string }> {
    return request('/skills', {
        method: 'POST',
        body: JSON.stringify(data),
    });
}

export async function updateSkillCode(namespace: string, name: string, code: string): Promise<{ updated: boolean }> {
    return request(`/skills/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/code`, {
        method: 'PUT',
        body: JSON.stringify({ code }),
    });
}

export async function updateSkillDefinition(
    namespace: string,
    name: string,
    data: { description?: string; parameters?: Record<string, unknown> },
): Promise<{ updated: boolean; definition: Record<string, unknown> }> {
    return request(`/skills/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/definition`, {
        method: 'PUT',
        body: JSON.stringify(data),
    });
}

export async function updateSkillReadme(namespace: string, name: string, readme: string): Promise<{ updated: boolean }> {
    return request(`/skills/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/readme`, {
        method: 'PUT',
        body: JSON.stringify({ readme }),
    });
}

export async function deleteSkill(namespace: string, name: string): Promise<{ deleted: boolean }> {
    return request(`/skills/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}`, {
        method: 'DELETE',
    });
}

// Knowledge
export interface KnowledgeDocSummary {
    path: string;
    subject: string;
    subsubject: string;
    title: string;
    updated_at: number;
}

export interface KnowledgeDoc extends KnowledgeDocSummary {
    content: string;
}

export type KnowledgeTree = Record<string, Record<string, string[]>>;

export async function getKnowledgeTree(): Promise<KnowledgeTree> {
    return request<KnowledgeTree>('/knowledge');
}

export async function getKnowledgeDoc(subject: string, subsubject: string, title: string): Promise<KnowledgeDoc> {
    return request<KnowledgeDoc>(`/knowledge/${encodeURIComponent(subject)}/${encodeURIComponent(subsubject)}/${encodeURIComponent(title)}`);
}

export async function searchKnowledge(q: string): Promise<KnowledgeDoc[]> {
    return request<KnowledgeDoc[]>(`/knowledge/search?q=${encodeURIComponent(q)}`);
}

export async function deleteKnowledgeDoc(subject: string, subsubject: string, title: string): Promise<void> {
    return request(`/knowledge/${encodeURIComponent(subject)}/${encodeURIComponent(subsubject)}/${encodeURIComponent(title)}/delete`, { method: 'POST' });
}

export async function updateKnowledgeDoc(subject: string, subsubject: string, title: string, content: string): Promise<KnowledgeDoc> {
    return request<KnowledgeDoc>(`/knowledge/${encodeURIComponent(subject)}/${encodeURIComponent(subsubject)}/${encodeURIComponent(title)}`, {
        method: 'PATCH',
        body: JSON.stringify({ content }),
    });
}
