import { useState, useEffect, useCallback } from 'react';
import {
    Box, VStack, HStack, Text, Heading, Badge, IconButton, Spinner, Code,
} from '@chakra-ui/react';

interface ServiceInfo {
    name: string;
    command: string;
    cwd: string;
    status: 'running' | 'stopped' | 'crashed';
    pid: number | null;
    uptime: number | null;
    exit_code: number | null;
    auto_start: boolean;
}

interface LogResponse {
    name: string;
    lines: string[];
    count: number;
}

const DEV_API = '/dev';

const PlayIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
        <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
);

const StopIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
        <rect x="6" y="6" width="12" height="12" />
    </svg>
);

const RestartIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
        <polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
);

const LogIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" /><polyline points="10 9 9 9 8 9" />
    </svg>
);

const STATUS_COLORS: Record<string, string> = {
    running: 'green',
    stopped: 'gray',
    crashed: 'red',
};

function formatUptime(seconds: number | null): string {
    if (seconds == null) return '-';
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}

const DevServices = () => {
    const [services, setServices] = useState<ServiceInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [expandedLogs, setExpandedLogs] = useState<Record<string, string[]>>({});
    const [busyActions, setBusyActions] = useState<Record<string, boolean>>({});

    const fetchServices = useCallback(async () => {
        try {
            const resp = await fetch(`${DEV_API}/services`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data: ServiceInfo[] = await resp.json();
            setServices(data);
            setError('');
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to reach dev spawner');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchServices();
    }, [fetchServices]);

    const doAction = async (name: string, action: 'start' | 'stop' | 'restart') => {
        setBusyActions(prev => ({ ...prev, [name]: true }));
        try {
            await fetch(`${DEV_API}/services/${name}/${action}`, { method: 'POST' });
            await fetchServices();
        } catch { /* ignore */ }
        setBusyActions(prev => ({ ...prev, [name]: false }));
    };

    const toggleLogs = async (name: string) => {
        if (expandedLogs[name]) {
            setExpandedLogs(prev => {
                const next = { ...prev };
                delete next[name];
                return next;
            });
            return;
        }
        try {
            const resp = await fetch(`${DEV_API}/services/${name}/logs?tail=100`);
            if (!resp.ok) return;
            const data: LogResponse = await resp.json();
            setExpandedLogs(prev => ({ ...prev, [name]: data.lines }));
        } catch { /* ignore */ }
    };

    if (loading) {
        return (
            <Box>
                <Heading size="md" color="var(--text-heading)" mb={4}>Dev Services</Heading>
                <HStack justify="center" py={4}><Spinner size="sm" /><Text>Loading...</Text></HStack>
            </Box>
        );
    }

    if (error) {
        return (
            <Box>
                <Heading size="md" color="var(--text-heading)" mb={4}>Dev Services</Heading>
                <Text color="var(--text-muted)" fontSize="sm">
                    Dev spawner not reachable ({error}). Start it with <Code>acai dev</Code>.
                </Text>
            </Box>
        );
    }

    return (
        <Box>
            <HStack justify="space-between" mb={4}>
                <Heading size="md" color="var(--text-heading)">Dev Services</Heading>
                <IconButton
                    aria-label="Refresh"
                    size="xs"
                    variant="ghost"
                    onClick={fetchServices}
                >
                    <RestartIcon />
                </IconButton>
            </HStack>

            <VStack gap={3} align="stretch">
                {services.map(svc => (
                    <Box
                        key={svc.name}
                        p={3}
                        bg="var(--bg-card)"
                        borderRadius="md"
                        border="1px solid"
                        borderColor="var(--border-primary)"
                    >
                        <HStack justify="space-between">
                            <HStack gap={3}>
                                <Badge colorScheme={STATUS_COLORS[svc.status] || 'gray'} fontSize="xs">
                                    {svc.status}
                                </Badge>
                                <Text fontWeight="semibold" color="var(--text-heading)" fontSize="sm">
                                    {svc.name}
                                </Text>
                                {svc.pid && (
                                    <Text fontSize="xs" color="var(--text-muted)">
                                        pid {svc.pid}
                                    </Text>
                                )}
                                {svc.uptime != null && (
                                    <Text fontSize="xs" color="var(--text-tertiary)">
                                        {formatUptime(svc.uptime)}
                                    </Text>
                                )}
                            </HStack>

                            <HStack gap={1}>
                                {svc.status !== 'running' && (
                                    <IconButton
                                        aria-label="Start"
                                        size="xs"
                                        variant="ghost"
                                        disabled={busyActions[svc.name]}
                                        onClick={() => doAction(svc.name, 'start')}
                                    >
                                        <PlayIcon />
                                    </IconButton>
                                )}
                                {svc.status === 'running' && (
                                    <IconButton
                                        aria-label="Stop"
                                        size="xs"
                                        variant="ghost"
                                        disabled={busyActions[svc.name]}
                                        onClick={() => doAction(svc.name, 'stop')}
                                    >
                                        <StopIcon />
                                    </IconButton>
                                )}
                                <IconButton
                                    aria-label="Restart"
                                    size="xs"
                                    variant="ghost"
                                    disabled={busyActions[svc.name]}
                                    onClick={() => doAction(svc.name, 'restart')}
                                >
                                    <RestartIcon />
                                </IconButton>
                                <IconButton
                                    aria-label="Logs"
                                    size="xs"
                                    variant="ghost"
                                    onClick={() => toggleLogs(svc.name)}
                                >
                                    <LogIcon />
                                </IconButton>
                            </HStack>
                        </HStack>

                        <Text fontSize="xs" color="var(--text-muted)" mt={1} fontFamily="mono">
                            {svc.command}
                        </Text>

                        {expandedLogs[svc.name] && (
                            <Box
                                mt={2}
                                p={2}
                                bg="var(--bg-tertiary)"
                                borderRadius="sm"
                                maxH="300px"
                                overflowY="auto"
                                fontFamily="mono"
                                fontSize="xs"
                            >
                                {expandedLogs[svc.name].length === 0 ? (
                                    <Text color="var(--text-muted)">(no output yet)</Text>
                                ) : (
                                    expandedLogs[svc.name].map((line, i) => (
                                        <Text key={i} whiteSpace="pre-wrap" color="var(--text-secondary)">
                                            {line}
                                        </Text>
                                    ))
                                )}
                            </Box>
                        )}
                    </Box>
                ))}
            </VStack>
        </Box>
    );
};

export default DevServices;
