import { createContext, useContext, useEffect, useRef, useState, ReactNode } from 'react';
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

interface WebSocketContextType {
    socket: Socket | null;
    isConnected: boolean;
    sessionId: string | null;
    on: (event: string, callback: (...args: any[]) => void) => () => void;
    off: (event: string, callback: (...args: any[]) => void) => void;
}

const WebSocketContext = createContext<WebSocketContextType | undefined>(undefined);

export const useWebSocket = () => {
    const context = useContext(WebSocketContext);
    if (!context) {
        throw new Error('useWebSocket must be used within a WebSocketProvider');
    }
    return context;
};

interface WebSocketProviderProps {
    children: ReactNode;
}

export const WebSocketProvider = ({ children }: WebSocketProviderProps) => {
    const [socket, setSocket] = useState<Socket | null>(null);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [isConnected, setIsConnected] = useState<boolean>(false);
    const listenersRef = useRef<Map<string, Set<(...args: any[]) => void>>>(new Map());

    useEffect(() => {
        // Determine WebSocket URL based on environment
        let wsUrl = import.meta.env.VITE_WS_URL;
        if (!wsUrl) {
            // In development, use relative path to go through Vite proxy
            // This works for both localhost and IP addresses
            if (import.meta.env.DEV) {
                wsUrl = undefined; // Use relative path - socket.io will use current origin
            } else {
                // In production, use the same hostname and protocol as the page
                const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
                const hostname = window.location.hostname;
                wsUrl = `${protocol}//${hostname}:5001`;
            }
        }

        console.log('[WebSocket] Connecting to:', wsUrl || 'current origin (via proxy)');

        const socketInstance = io(wsUrl, {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionAttempts: 5,
            autoConnect: true,
        });

        setSocket(socketInstance);

        socketInstance.on('connect', () => {
            console.log('[WebSocket] Connected successfully');
            setIsConnected(true);
            // Generate a unique session ID
            const newSessionId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
            setSessionId(newSessionId);

            // Join the session room
            socketInstance.emit('join_session', { session_id: newSessionId }, (response: any) => {
                if (response?.status === 'joined') {
                    console.log('[WebSocket] Joined session:', newSessionId);
                }
            });
        });

        socketInstance.on('disconnect', (reason) => {
            console.log('[WebSocket] Disconnected:', reason);
            setIsConnected(false);
        });

        socketInstance.on('connect_error', (error) => {
            console.error('[WebSocket] Connection error:', error.message);
        });

        socketInstance.on('reconnect_attempt', (attemptNumber) => {
            console.log(`[WebSocket] Reconnection attempt ${attemptNumber}`);
        });

        socketInstance.on('reconnect_failed', () => {
            console.error('[WebSocket] Reconnection failed after all attempts');
        });

        // Cleanup on unmount
        return () => {
            console.log('[WebSocket] Cleaning up connection');
            socketInstance.disconnect();
            setSocket(null);
            setSessionId(null);
            setIsConnected(false);
            listenersRef.current.clear();
        };
    }, []);

    // Helper function to register event listeners
    const on = (event: string, callback: (...args: any[]) => void) => {
        if (!socket) {
            console.warn(`[WebSocket] Cannot register listener for ${event}: socket not initialized`);
            return () => { };
        }

        // Track listener for cleanup
        if (!listenersRef.current.has(event)) {
            listenersRef.current.set(event, new Set());
        }
        listenersRef.current.get(event)!.add(callback);

        // Register with socket.io
        socket.on(event, callback);

        // Return unsubscribe function
        return () => {
            off(event, callback);
        };
    };

    // Helper function to unregister event listeners
    const off = (event: string, callback: (...args: any[]) => void) => {
        if (!socket) {
            return;
        }

        // Remove from tracking
        const listeners = listenersRef.current.get(event);
        if (listeners) {
            listeners.delete(callback);
            if (listeners.size === 0) {
                listenersRef.current.delete(event);
            }
        }

        // Unregister from socket.io
        socket.off(event, callback);
    };

    const value: WebSocketContextType = {
        socket,
        isConnected,
        sessionId,
        on,
        off,
    };

    return (
        <WebSocketContext.Provider value={value}>
            {children}
        </WebSocketContext.Provider>
    );
};

