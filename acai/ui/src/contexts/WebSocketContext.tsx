import { createContext, useContext, useEffect, useState, useCallback, useMemo, ReactNode } from 'react';
import { io, Socket } from 'socket.io-client';
import type { Task, AgentEvent, AgentStatus, Capabilities, TelemetryData } from '../services/types';
import { toaster } from '../components/ui/toaster';

interface AgentSocketContextType {
    socket: Socket | null;
    isConnected: boolean;
    tasks: Task[];
    status: AgentStatus | null;
    events: AgentEvent[];
    capabilities: Capabilities | null;
    telemetry: TelemetryData | null;
    requestTelemetry: () => void;
    joinConversation: (convId: string) => void;
    leaveConversation: (convId: string) => void;
}

const AgentSocketContext = createContext<AgentSocketContextType | undefined>(undefined);

export const useAgentSocket = () => {
    const ctx = useContext(AgentSocketContext);
    if (!ctx) throw new Error('useAgentSocket must be used within AgentSocketProvider');
    return ctx;
};

export const AgentSocketProvider = ({ children }: { children: ReactNode }) => {
    const [socket, setSocket] = useState<Socket | null>(null);
    const [isConnected, setIsConnected] = useState(false);
    const [tasks, setTasks] = useState<Task[]>([]);
    const [status, setStatus] = useState<AgentStatus | null>(null);
    const [events, setEvents] = useState<AgentEvent[]>([]);
    const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
    const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);

    useEffect(() => {
        const wsUrl: string | undefined = import.meta.env.VITE_WS_URL;

        const sock = io(wsUrl || window.location.origin, {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionAttempts: Infinity,
            autoConnect: true,
        });

        setSocket(sock);

        sock.on('connect', () => setIsConnected(true));
        sock.on('disconnect', () => setIsConnected(false));

        sock.on('tasks', (data: Task[]) => setTasks(data));
        sock.on('status', (data: AgentStatus) => setStatus(data));
        sock.on('events', (data: AgentEvent[]) => setEvents(data));
        sock.on('capabilities', (data: Capabilities) => setCapabilities(data));
        sock.on('telemetry', (data: TelemetryData) => setTelemetry(data));

        sock.on('toast', (data: { message: string; title?: string; status?: string; duration?: number }) => {
            const type = (data.status === 'success' || data.status === 'error' || data.status === 'warning')
                ? data.status : 'info';
            toaster.create({
                title: data.title || undefined,
                description: data.message,
                type,
                duration: data.duration || 5000,
            });
        });

        return () => {
            sock.disconnect();
            setSocket(null);
            setIsConnected(false);
        };
    }, []);

    const requestTelemetry = useCallback(() => {
        socket?.emit('request_telemetry');
    }, [socket]);

    const joinConversation = useCallback((convId: string) => {
        socket?.emit('join_conversation', { conversation: convId });
    }, [socket]);

    const leaveConversation = useCallback((convId: string) => {
        socket?.emit('leave_conversation', { conversation: convId });
    }, [socket]);

    return (
        <AgentSocketContext.Provider value={{
            socket, isConnected, tasks, status, events,
            capabilities, telemetry, requestTelemetry,
            joinConversation, leaveConversation,
        }}>
            {children}
        </AgentSocketContext.Provider>
    );
};

/**
 * Backward-compatible hook for legacy components that import `useWebSocket`.
 * Maps onto the agent socket context, providing `socket`, `isConnected`, and
 * a stub `sessionId`.
 */
export const useWebSocket = () => {
    const ctx = useAgentSocket();
    return useMemo(() => ({
        socket: ctx.socket,
        isConnected: ctx.isConnected,
        sessionId: '',
    }), [ctx.socket, ctx.isConnected]);
};
