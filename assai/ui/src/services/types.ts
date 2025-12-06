// Type definitions for ASSAI API

export interface Message {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
    type?: 'text' | 'image' | 'audio';
    imageUrl?: string;
    audioUrl?: string;
    retryPrompt?: string; // Store the prompt for retry functionality
    actionId?: number; // Action ID for linking logs to this message
    logs?: Array<{ type: 'stdout' | 'stderr'; line: string; timestamp: Date }>; // Logs for this action
    isGenerating?: boolean; // Whether generation is in progress
}

export interface Conversation {
    id: string;
    title: string;
    messages: Message[];
    createdAt: Date;
    updatedAt: Date;
}

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

