// Type definitions for ASSAI Agent Swarm

export interface AgentMessage {
    role: 'user' | 'assistant' | 'tool_call' | 'tool_result' | 'phase' | 'print';
    content: string;
    reasoning?: string;
    name?: string;
    /** Phase name for role='phase' messages (e.g. "curator", "scribe"). */
    phase?: string;
    /** Phase status: "start", "end", "tool_start", "tool_end". */
    phaseStatus?: string;
    /** Label for print messages (the originating node name). */
    nodeLabel?: string;
    isStreaming?: boolean;
    taskId?: string;
    error?: string;
}

export interface StreamChunk {
    task_id: string;
    token: string;
    index: number;
}

export interface ToolStartEvent {
    conversation: string;
    tool_name: string;
    args: Record<string, unknown>;
}

export interface ToolEndEvent {
    conversation: string;
    tool_name: string;
    result_preview: string;
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
    started_at: string;
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
    /** Default agent slug for project chat (task refinement, etc.). */
    refiner?: string;
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
    description: string;
    project: string;
    task_id?: string;
    provider: string;
    agent: string;
    tags: string[];
    /** Present when `project` is set: copy of that project's `refiner` agent slug. */
    refiner?: string;
    enable_thinking?: boolean;
    created_at: number;
}

export interface SandboxConfig {
    // Common — all backends
    type: string;
    network: boolean;
    gpu: boolean;
    timeout: number;
    memory_limit: string;
    writable_paths: string[];
    readonly_paths: string[];

    // Container (docker / podman)
    image: string;
    runtime: string;

    // Firecracker (microVM)
    kernel: string;
    rootfs: string;
    vcpu_count: number;
    firecracker_bin: string;

    // Bubblewrap
    unshare_user: boolean;
    unshare_pid: boolean;
    unshare_ipc: boolean;
    dev_mode: string;

    // Nsjail
    nsjail_config: string;
    cgroup_pids_max: number;
    rlimit_as: string;
    seccomp_policy: string;

    // System-level
    mcp_port: number;
}

export interface WorkerConfig {
    max_retries: number;
    timeout: number;
    tasks_dir: string;
    host: string;
    port: number;
    orchestrator_url: string;
}

export interface GitConfig {
    repo_path: string;
    worktree_dir: string;
    auto_commit: boolean;
}

export interface QueueConfig {
    url: string;
    poll_interval: number;
    task_timeout: number;
}

export interface AuditConfig {
    enabled: boolean;
    dir: string;
}

export interface SystemConfig {
    workspace: string;
    sandbox: SandboxConfig;
    worker: WorkerConfig;
    git: GitConfig;
    queue: QueueConfig;
    audit: AuditConfig;
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
    tool_permissions: string[];
    uses_sandbox: boolean;
    max_iterations: number;
    approval_required: boolean;
    created_at: string;
    tags: string[];
    builtin: boolean;
}
