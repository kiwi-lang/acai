// Type definitions for ASSAI Agent Swarm

export interface AgentMessage {
    role: 'user' | 'assistant';
    content: string;
    isStreaming?: boolean;
    taskId?: string;
}

export interface StreamChunk {
    task_id: string;
    token: string;
    index: number;
}

export interface Capabilities {
    telemetry: boolean;
}

export interface TelemetryData {
    cpu: {
        load: number;
        memory: number[];
    };
    gpu: Record<string, {
        load: number;
        memory: number[];
        power: number;
        temperature: number;
    }>;
    network: {
        bytes_sent: number;
        bytes_recv: number;
    };
    disk: Record<string, any>;
    time: number;
}

export interface Task {
    id: string;
    kind: string;
    gpu: number;
    title: string;
    description: string;
    status: string;
    priority: number;
    spec: string;
    spec_path: string;
    context_path: string;
    result_path: string;
    worktree: string;
    retries: number;
    max_retries: number;
    created_at: string;
    updated_at: string;
    assigned_to: string;
    depends_on: string;
    error_log: string;
    project: string;
    agent: string;
    parent_task: string;
    root_task: string;
}

export interface AgentEvent {
    kind: string;
    source: string;
    data: Record<string, any>;
    timestamp: string;
}

export interface AgentStatus {
    queue: Record<string, number>;
    events: number;
    conversation_turns: number;
    llm_backend: string;
    llm_endpoint: string;
    active_provider: string;
    providers_count: number;
}

export interface Provider {
    name: string;
    backend: string;
    model: string;
    slug: string;
    endpoint: string;
    api_key: string;
    server_port: number;
    server_command: string;
    max_tokens: number;
    temperature: number;
    priority: number;
    roles: string[];
    active?: boolean;
}

export interface Worktree {
    path: string;
    branch: string;
    head: string;
}

export interface Project {
    id: string;
    name: string;
    language: string;
    source: string;
    template: string;
    repo_url: string;
    provider: string;
    path: string;
    python_version: string;
    venv_path: string;
    created_at: string;
}

// Kept for ChatComponent compatibility
export interface Input {
    kind: 'text' | 'image' | 'audio' | 'video' | 'mesh';
    encoding: string;
    data: string;
}

export interface Message {
    id: number | string;
    action_id?: number;
    role: 'user' | 'assistant';
    content: Input;
    timestamp: string | Date;
    logs?: Array<{ type: 'stdout' | 'stderr'; line: string; timestamp: Date }>;
    isGenerating?: boolean;
    retryPrompt?: string;
    type?: 'text' | 'image' | 'audio' | 'video' | 'mesh';
    imageUrl?: string;
    imageUrls?: string[];
    audioUrl?: string;
    videoUrl?: string;
    meshUrl?: string;
}

export interface Conversation {
    id: string;
    title: string;
    messages: Message[];
}

export interface ConversationMeta {
    id: string;
    title: string;
    project: string;
    provider: string;
    agent: string;
    created_at: number;
}

export interface SandboxConfig {
    type: string;
    network: boolean;
    writable_paths: string[];
    readonly_paths: string[];
    gpu: boolean;
    timeout: number;
    memory_limit: string;
}

export interface AgentDef {
    id: string;
    name: string;
    description: string;
    role: string;
    avatar: string;
    provider: string;
    output_format: 'messages' | 'text';
    model_overrides: Record<string, any>;
    system_template: string;
    context_sources: string[];
    tools: string[];
    sandbox: SandboxConfig;
    max_iterations: number;
    approval_required: boolean;
    created_at: string;
    tags: string[];
}
