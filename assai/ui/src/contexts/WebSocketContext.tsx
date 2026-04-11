import { createContext, useContext, useEffect, useState, useCallback, useRef, useMemo, ReactNode } from 'react';
import { io, Socket } from 'socket.io-client';
import type { Task, AgentEvent, AgentStatus, StreamChunk, Capabilities, TelemetryData, ToolStartEvent, ToolEndEvent } from '../services/types';
import { toaster } from '../components/ui/toaster';

interface StreamError {
    task_id: string;
    error: string;
}

interface AgentSocketContextType {
    socket: Socket | null;
    isConnected: boolean;
    tasks: Task[];
    status: AgentStatus | null;
    events: AgentEvent[];
    capabilities: Capabilities | null;
    telemetry: TelemetryData | null;
    requestTelemetry: () => void;
    onChunk: (cb: (chunk: StreamChunk) => void) => () => void;
    onStreamEnd: (cb: (data: { task_id: string }) => void) => () => void;
    onStreamError: (cb: (data: StreamError) => void) => () => void;
    onToolStart: (cb: (data: ToolStartEvent) => void) => () => void;
    onToolEnd: (cb: (data: ToolEndEvent) => void) => () => void;
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

    const chunkListeners = useRef<Set<(chunk: StreamChunk) => void>>(new Set());
    const endListeners = useRef<Set<(data: { task_id: string }) => void>>(new Set());
    const errorListeners = useRef<Set<(data: StreamError) => void>>(new Set());
    const toolStartListeners = useRef<Set<(data: ToolStartEvent) => void>>(new Set());
    const toolEndListeners = useRef<Set<(data: ToolEndEvent) => void>>(new Set());

    useEffect(() => {
        let wsUrl: string | undefined = import.meta.env.VITE_WS_URL;
        if (!wsUrl && !import.meta.env.DEV) {
            const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
            wsUrl = `${protocol}//${window.location.hostname}:5050`;
        }

        const sock = io(wsUrl, {
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

        sock.on('chunk', (data: StreamChunk) => {
            chunkListeners.current.forEach(cb => cb(data));
        });
        sock.on('stream_end', (data: { task_id: string }) => {
            endListeners.current.forEach(cb => cb(data));
        });
        sock.on('stream_error', (data: StreamError) => {
            errorListeners.current.forEach(cb => cb(data));
        });

        sock.on('tool_start', (data: ToolStartEvent) => {
            toolStartListeners.current.forEach(cb => cb(data));
        });
        sock.on('tool_end', (data: ToolEndEvent) => {
            toolEndListeners.current.forEach(cb => cb(data));
        });

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

    const onChunk = useCallback((cb: (chunk: StreamChunk) => void) => {
        chunkListeners.current.add(cb);
        return () => { chunkListeners.current.delete(cb); };
    }, []);

    const onStreamEnd = useCallback((cb: (data: { task_id: string }) => void) => {
        endListeners.current.add(cb);
        return () => { endListeners.current.delete(cb); };
    }, []);

    const onStreamError = useCallback((cb: (data: StreamError) => void) => {
        errorListeners.current.add(cb);
        return () => { errorListeners.current.delete(cb); };
    }, []);

    const onToolStart = useCallback((cb: (data: ToolStartEvent) => void) => {
        toolStartListeners.current.add(cb);
        return () => { toolStartListeners.current.delete(cb); };
    }, []);

    const onToolEnd = useCallback((cb: (data: ToolEndEvent) => void) => {
        toolEndListeners.current.add(cb);
        return () => { toolEndListeners.current.delete(cb); };
    }, []);

    return (
        <AgentSocketContext.Provider value={{
            socket, isConnected, tasks, status, events,
            capabilities, telemetry, requestTelemetry,
            onChunk, onStreamEnd, onStreamError,
            onToolStart, onToolEnd,
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
