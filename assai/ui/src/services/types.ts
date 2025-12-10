// Type definitions for ASSAI API
// Base format matches backend assai.tools.input exactly

// Input format (matches backend assai.tools.input.Input)
export interface Input {
    kind: 'text' | 'image' | 'audio' | 'video' | 'mesh';
    encoding: string;
    data: string;
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
    type?: 'text' | 'image' | 'audio' | 'video' | 'mesh';
    imageUrl?: string;
    imageUrls?: string[];
    audioUrl?: string;
    videoUrl?: string;
    meshUrl?: string;
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

