// WebSocket service for real-time updates
import { io, Socket } from 'socket.io-client';

export type WebSocketMessageType =
    | 'model_loading'
    | 'model_loaded'
    | 'generation_started'
    | 'generation_progress'
    | 'generation_complete'
    | 'error';

export interface WebSocketMessage {
    type: WebSocketMessageType;
    data: {
        message?: string;
        model?: string;
        prompt?: string;
        step?: number;
        total_steps?: number;
        progress?: number;
        images?: string[];
        error?: string;
    };
}

class WebSocketService {
    private socket: Socket | null = null;
    private sessionId: string | null = null;
    private listeners: Map<string, Set<(message: WebSocketMessage) => void>> = new Map();

    connect(): void {
        if (this.socket?.connected) {
            return;
        }

        // Determine WebSocket URL based on environment
        const wsUrl = import.meta.env.VITE_WS_URL || window.location.origin;

        this.socket = io(wsUrl, {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionAttempts: 5,
        });

        this.socket.on('connect', () => {
            console.log('WebSocket connected');
            // Generate a unique session ID
            this.sessionId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

            // Join the session room
            if (this.sessionId) {
                this.socket?.emit('join_session', { session_id: this.sessionId });
            }
        });

        this.socket.on('disconnect', () => {
            console.log('WebSocket disconnected');
        });

        this.socket.on('text2image_update', (message: WebSocketMessage) => {
            // Notify all listeners for this message type
            const typeListeners = this.listeners.get(message.type);
            if (typeListeners) {
                typeListeners.forEach(listener => listener(message));
            }

            // Also notify general listeners
            const allListeners = this.listeners.get('*');
            if (allListeners) {
                allListeners.forEach(listener => listener(message));
            }
        });

        this.socket.on('connect_error', (error) => {
            console.error('WebSocket connection error:', error);
        });
    }

    disconnect(): void {
        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
            this.sessionId = null;
        }
    }

    getSessionId(): string | null {
        return this.sessionId;
    }

    isConnected(): boolean {
        return this.socket?.connected || false;
    }

    on(messageType: WebSocketMessageType | '*', callback: (message: WebSocketMessage) => void): () => void {
        if (!this.listeners.has(messageType)) {
            this.listeners.set(messageType, new Set());
        }
        this.listeners.get(messageType)!.add(callback);

        // Return unsubscribe function
        return () => {
            const listeners = this.listeners.get(messageType);
            if (listeners) {
                listeners.delete(callback);
                if (listeners.size === 0) {
                    this.listeners.delete(messageType);
                }
            }
        };
    }

    off(messageType: WebSocketMessageType | '*', callback: (message: WebSocketMessage) => void): void {
        const listeners = this.listeners.get(messageType);
        if (listeners) {
            listeners.delete(callback);
            if (listeners.size === 0) {
                this.listeners.delete(messageType);
            }
        }
    }
}

// Export singleton instance
export const websocketService = new WebSocketService();

