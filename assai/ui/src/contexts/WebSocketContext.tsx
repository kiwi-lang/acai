import { createContext, useContext, useEffect, useState, useCallback, ReactNode } from 'react';
import { io, Socket } from 'socket.io-client';
import type { Task, AgentEvent, AgentStatus } from '../services/types';

interface AgentSocketContextType {
    socket: Socket | null;
    isConnected: boolean;
    tasks: Task[];
    status: AgentStatus | null;
    events: AgentEvent[];
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

        return () => {
            sock.disconnect();
            setSocket(null);
            setIsConnected(false);
        };
    }, []);

    return (
        <AgentSocketContext.Provider value={{ socket, isConnected, tasks, status, events }}>
            {children}
        </AgentSocketContext.Provider>
    );
};
