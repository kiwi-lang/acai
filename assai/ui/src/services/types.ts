// Type definitions for ASSAI API
// Base format matches backend assai.tools.input exactly

// Graph data structure
export interface GraphNode {
    id: string;
    label?: string;
    features?: number[];
    properties?: Record<string, any>;
    x?: number; // For layout
    y?: number; // For layout
    z?: number; // For 3D layout
    color?: string;
    size?: number;
}

export interface GraphEdge {
    source: string;
    target: string;
    weight?: number;
    properties?: Record<string, any>;
    color?: string;
    width?: number;
}

export interface GraphData {
    nodes: GraphNode[];
    edges: GraphEdge[];
    metadata?: {
        type?: string;
        predictions?: Record<string, any>;
        layout?: string;
    };
}

// Input format (matches backend assai.tools.input.Input)
export interface Input {
    kind: 'text' | 'image' | 'audio' | 'video' | 'mesh' | 'graph';
    encoding: string;
    data: string; // JSON string for graph, base64 for images/audio/video, etc.
}

// Message format (matches backend assai.tools.input.Message)
// Frontend can extend this for UI state, but base format must match backend
export interface Message {
    id: number | string; // Backend uses number, frontend can use string for UI
    action_id?: number; // Optional - only present for assistant messages with action_id
    role: 'user' | 'assistant';
    content: Input; // Matches backend - content is Input, not string
    timestamp: string | Date; // Backend uses datetime ISO string, frontend can use Date
    // Frontend extensions (not sent to backend):
    logs?: Array<{ type: 'stdout' | 'stderr'; line: string; timestamp: Date }>;
    isGenerating?: boolean;
    retryPrompt?: string; // For retry functionality
    // UI display extensions (extracted from Input for convenience)
    type?: 'text' | 'image' | 'audio' | 'video' | 'mesh' | 'graph';
    imageUrl?: string;
    imageUrls?: string[];
    audioUrl?: string;
    videoUrl?: string;
    meshUrl?: string;
    graphData?: GraphData;
}

// Conversation format (matches backend assai.tools.input.Conversation)
// This is the base format used for API communication
export interface Conversation {
    messages: Message[];
}

// Extended Conversation format for UI state management
export interface ConversationWithMetadata {
    id: string;
    title: string;
    messages: Message[];
    createdAt: Date;
    updatedAt: Date;
}

// Multimodal types (aliases for unified format)
export type MultimodalConversation = Conversation;
export type MultimodalMessage = Message;

export interface ModelPlugin {
    name: string;
    type: string;
    input: string;
    output: string;
    description?: string;
}

export interface ChatRequest {
    message: string;
    conversationId?: string;
    model?: string;
}

export interface ChatResponse {
    message: Message;
    conversationId: string;
}

