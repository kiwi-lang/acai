// Type definitions for ASSAI Agent Swarm

export interface AgentMessage {
    role: 'user' | 'assistant';
    content: string;
}

export interface Task {
    id: string;
    kind: string;
    gpu: number;
    title: string;
    description: string;
    status: string;
    priority: number;
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
}

export interface Worktree {
    path: string;
    branch: string;
    head: string;
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
