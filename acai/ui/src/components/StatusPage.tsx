import { useState, useEffect, useCallback } from 'react';
import {
    Box, VStack, HStack, Text, Heading, Badge, IconButton, Input,
    NativeSelect, Spinner,
} from '@chakra-ui/react';
import {
    getStatus, listEvents, listProviders, createProvider,
    updateProvider, deleteProvider, activateProvider,
} from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { AgentEvent, AgentStatus, Provider } from '../services/types';

const EVENT_COLORS: Record<string, string> = {
    new_requirement: 'blue',
    clarification: 'cyan',
    decision: 'green',
    contradiction: 'red',
    task_request: 'orange',
    spec_updated: 'teal',
    spec_created: 'teal',
    task_created: 'yellow',
    task_assigned: 'purple',
    context_ready: 'blue',
    task_completed: 'green',
    task_failed: 'red',
    task_needs_review: 'orange',
};

const BACKENDS = ['vllm', 'openai', 'anthropic', 'llamacpp'];
const ROLES = ['worker', 'curator', 'manager'];

const PlusIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
        <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
);

const TrashIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14H6L5 6" />
        <path d="M10 11v6" /><path d="M14 11v6" /><path d="M9 6V4h6v2" />
    </svg>
);

const EditIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
);

interface ProviderFormData {
    name: string;
    backend: string;
    model: string;
    endpoint: string;
    api_key: string;
    server_port: string;
    max_tokens: string;
    temperature: string;
    priority: string;
    roles: string[];
}

const emptyForm: ProviderFormData = {
    name: '', backend: 'openai', model: '', endpoint: '',
    api_key: '', server_port: '9123', max_tokens: '4096',
    temperature: '0.7', priority: '50', roles: [],
};

const StatusPage = () => {
    const { status: wsStatus, events: wsEvents, isConnected } = useAgentSocket();
    const [status, setStatus] = useState<AgentStatus | null>(null);
    const [events, setEvents] = useState<AgentEvent[]>([]);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [error, setError] = useState('');
    const [showForm, setShowForm] = useState(false);
    const [editingName, setEditingName] = useState<string | null>(null);
    const [form, setForm] = useState<ProviderFormData>(emptyForm);
    const [formError, setFormError] = useState('');
    const [busy, setBusy] = useState(false);

    const refreshProviders = useCallback(() => {
        listProviders().then(setProviders).catch(() => {});
    }, []);

    useEffect(() => { if (wsStatus) setStatus(wsStatus); }, [wsStatus]);
    useEffect(() => { if (wsEvents.length > 0) setEvents([...wsEvents].reverse()); }, [wsEvents]);

    useEffect(() => {
        document.title = 'Status - Açaí';
        if (!isConnected) {
            Promise.all([getStatus(), listEvents(100)])
                .then(([s, e]) => { setStatus(s); setEvents(e.reverse()); setError(''); })
                .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'));
        }
        refreshProviders();
    }, [isConnected, refreshProviders]);

    const openAdd = () => {
        setForm(emptyForm);
        setEditingName(null);
        setFormError('');
        setShowForm(true);
    };

    const openEdit = (p: Provider) => {
        setForm({
            name: p.name,
            backend: p.backend,
            model: p.model,
            endpoint: p.endpoint,
            api_key: p.api_key,
            server_port: String(p.server_port),
            max_tokens: String(p.max_tokens),
            temperature: String(p.temperature),
            priority: String(p.priority),
            roles: [...p.roles],
        });
        setEditingName(p.name);
        setFormError('');
        setShowForm(true);
    };

    const handleSubmit = async () => {
        if (!form.name.trim()) { setFormError('Name is required'); return; }
        setBusy(true);
        setFormError('');
        try {
            const payload = {
                name: form.name.trim(),
                backend: form.backend,
                model: form.model,
                endpoint: form.endpoint,
                api_key: form.api_key,
                server_port: parseInt(form.server_port) || 9123,
                max_tokens: parseInt(form.max_tokens) || 4096,
                temperature: parseFloat(form.temperature) || 0.7,
                priority: parseInt(form.priority) || 0,
                roles: form.roles,
            };
            if (editingName) {
                await updateProvider(editingName, payload);
            } else {
                await createProvider(payload);
            }
            setShowForm(false);
            refreshProviders();
        } catch (err) {
            setFormError(err instanceof Error ? err.message : 'Failed');
        } finally {
            setBusy(false);
        }
    };

    const handleDelete = async (name: string) => {
        setBusy(true);
        try {
            await deleteProvider(name);
            refreshProviders();
        } catch { /* ignore */ } finally { setBusy(false); }
    };

    const handleActivate = async (name: string) => {
        setBusy(true);
        try {
            await activateProvider(name);
            refreshProviders();
        } catch { /* ignore */ } finally { setBusy(false); }
    };

    const toggleRole = (role: string) => {
        setForm(prev => ({
            ...prev,
            roles: prev.roles.includes(role)
                ? prev.roles.filter(r => r !== role)
                : [...prev.roles, role],
        }));
    };

    const setField = (key: keyof ProviderFormData, value: string) =>
        setForm(prev => ({ ...prev, [key]: value }));

    return (
        <Box h="100vh" w="100%" bg="var(--bg-page)" overflowY="auto" p={6}>
            <Box maxW="4xl" mx="auto">
                <HStack justify="space-between" mb={6}>
                    <Heading size="lg" color="var(--text-heading)">Worker Status</Heading>
                    <Badge colorScheme={isConnected ? 'green' : 'red'} fontSize="xs" variant="outline">
                        {isConnected ? 'live' : 'disconnected'}
                    </Badge>
                </HStack>

                {error && (
                    <Box p={3} bg="var(--bg-error)" borderRadius="md" mb={4}>
                        <Text color="var(--text-error)" fontSize="sm">{error}</Text>
                    </Box>
                )}

                {/* Providers */}
                <Box mb={8}>
                    <HStack justify="space-between" mb={3}>
                        <Text fontWeight="semibold" color="var(--text-heading)" fontSize="lg">
                            Providers
                        </Text>
                        <IconButton
                            aria-label="Add provider"
                            size="sm"
                            variant="outline"
                            onClick={openAdd}
                            borderColor="var(--border-primary)"
                            color="var(--text-primary)"
                        >
                            <PlusIcon />
                        </IconButton>
                    </HStack>

                    {providers.length === 0 && !showForm && (
                        <Text color="var(--text-muted)" fontSize="sm" py={4} textAlign="center">
                            No providers configured
                        </Text>
                    )}

                    <VStack gap={3} align="stretch">
                        {providers.map(p => (
                            <Box
                                key={p.name}
                                p={4}
                                bg="var(--bg-card)"
                                borderRadius="lg"
                                border="2px solid"
                                borderColor={p.active ? 'var(--accent)' : 'var(--border-primary)'}
                            >
                                <HStack justify="space-between" mb={2}>
                                    <HStack gap={2}>
                                        <Text fontWeight="bold" color="var(--text-heading)" fontSize="sm">
                                            {p.name}
                                        </Text>
                                        {p.active && (
                                            <Badge colorScheme="green" fontSize="2xs">active</Badge>
                                        )}
                                        <Badge variant="outline" fontSize="2xs">{p.backend}</Badge>
                                    </HStack>
                                    <HStack gap={1}>
                                        {!p.active && (
                                            <IconButton
                                                aria-label="Activate"
                                                size="xs"
                                                variant="ghost"
                                                onClick={() => handleActivate(p.name)}
                                                color="var(--accent)"
                                                title="Activate this provider"
                                                disabled={busy}
                                            >
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                                                    <path d="M8 5v14l11-7z" />
                                                </svg>
                                            </IconButton>
                                        )}
                                        <IconButton
                                            aria-label="Edit" size="xs" variant="ghost"
                                            onClick={() => openEdit(p)}
                                            color="var(--text-tertiary)"
                                        >
                                            <EditIcon />
                                        </IconButton>
                                        <IconButton
                                            aria-label="Delete" size="xs" variant="ghost"
                                            onClick={() => handleDelete(p.name)}
                                            color="var(--text-error)" disabled={busy}
                                        >
                                            <TrashIcon />
                                        </IconButton>
                                    </HStack>
                                </HStack>

                                <HStack gap={6} flexWrap="wrap">
                                    <VStack align="flex-start" gap={0}>
                                        <Text fontSize="xs" color="var(--text-muted)">Model</Text>
                                        <Text fontSize="sm" color="var(--text-primary)" fontFamily="mono">
                                            {p.model || p.slug || '—'}
                                        </Text>
                                    </VStack>
                                    <VStack align="flex-start" gap={0}>
                                        <Text fontSize="xs" color="var(--text-muted)">Endpoint</Text>
                                        <Text fontSize="sm" color="var(--text-primary)" fontFamily="mono">
                                            {p.endpoint || '—'}
                                        </Text>
                                    </VStack>
                                    <VStack align="flex-start" gap={0}>
                                        <Text fontSize="xs" color="var(--text-muted)">Priority</Text>
                                        <Text fontSize="sm" color="var(--text-primary)">{p.priority}</Text>
                                    </VStack>
                                </HStack>

                                {p.roles.length > 0 && (
                                    <HStack mt={2} gap={1}>
                                        <Text fontSize="xs" color="var(--text-muted)">Roles:</Text>
                                        {p.roles.map(r => (
                                            <Badge key={r} fontSize="2xs" variant="outline">
                                                {r}
                                            </Badge>
                                        ))}
                                    </HStack>
                                )}
                            </Box>
                        ))}
                    </VStack>

                    {/* Add/Edit form */}
                    {showForm && (
                        <Box
                            mt={3} p={4} bg="var(--bg-elevated)" borderRadius="lg"
                            border="1px solid" borderColor="var(--border-primary)"
                        >
                            <Text fontWeight="semibold" color="var(--text-heading)" mb={3} fontSize="sm">
                                {editingName ? `Edit "${editingName}"` : 'New Provider'}
                            </Text>

                            {formError && (
                                <Box p={2} bg="var(--bg-error)" borderRadius="md" mb={3}>
                                    <Text color="var(--text-error)" fontSize="xs">{formError}</Text>
                                </Box>
                            )}

                            <VStack gap={3} align="stretch">
                                <HStack gap={3}>
                                    <Box flex={1}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Name</Text>
                                        <Input
                                            size="sm" placeholder="e.g. claude-sonnet"
                                            value={form.name}
                                            onChange={e => setField('name', e.target.value)}
                                            disabled={!!editingName}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        />
                                    </Box>
                                    <Box flex={1}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Backend</Text>
                                        <NativeSelect.Root size="sm">
                                            <NativeSelect.Field
                                                value={form.backend}
                                                onChange={e => setField('backend', e.target.value)}
                                                bg="var(--bg-input)" color="var(--text-primary)"
                                                borderColor="var(--border-input)"
                                            >
                                                {BACKENDS.map(b => (
                                                    <option key={b} value={b} style={{ background: 'var(--option-bg)' }}>
                                                        {b}
                                                    </option>
                                                ))}
                                            </NativeSelect.Field>
                                        </NativeSelect.Root>
                                    </Box>
                                </HStack>

                                <HStack gap={3}>
                                    <Box flex={2}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Model</Text>
                                        <Input
                                            size="sm" placeholder="Qwen/Qwen3-Coder-Next-FP8"
                                            value={form.model}
                                            onChange={e => setField('model', e.target.value)}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        />
                                    </Box>
                                    <Box flex={1}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Priority</Text>
                                        <Input
                                            size="sm" type="number" placeholder="50"
                                            value={form.priority}
                                            onChange={e => setField('priority', e.target.value)}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        />
                                    </Box>
                                </HStack>

                                <Box>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>Endpoint</Text>
                                    <Input
                                        size="sm" placeholder="https://api.openai.com"
                                        value={form.endpoint}
                                        onChange={e => setField('endpoint', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                    />
                                </Box>

                                <Box>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>API Key</Text>
                                    <Input
                                        size="sm" type="password" placeholder="sk-..."
                                        value={form.api_key}
                                        onChange={e => setField('api_key', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                    />
                                </Box>

                                <HStack gap={3}>
                                    <Box flex={1}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Server Port</Text>
                                        <Input
                                            size="sm" type="number" placeholder="9123"
                                            value={form.server_port}
                                            onChange={e => setField('server_port', e.target.value)}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        />
                                    </Box>
                                    <Box flex={1}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Max Tokens</Text>
                                        <Input
                                            size="sm" type="number" placeholder="4096"
                                            value={form.max_tokens}
                                            onChange={e => setField('max_tokens', e.target.value)}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        />
                                    </Box>
                                    <Box flex={1}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Temperature</Text>
                                        <Input
                                            size="sm" type="number" step="0.1" placeholder="0.7"
                                            value={form.temperature}
                                            onChange={e => setField('temperature', e.target.value)}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        />
                                    </Box>
                                </HStack>

                                <Box>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>
                                        Roles (click to toggle, order = preference)
                                    </Text>
                                    <HStack gap={2}>
                                        {ROLES.map(role => (
                                            <Box
                                                key={role}
                                                as="button"
                                                px={3} py={1}
                                                borderRadius="md"
                                                fontSize="xs"
                                                fontWeight="medium"
                                                border="1px solid"
                                                borderColor={form.roles.includes(role) ? 'var(--accent)' : 'var(--border-primary)'}
                                                bg={form.roles.includes(role) ? 'var(--accent-subtle)' : 'transparent'}
                                                color={form.roles.includes(role) ? 'var(--accent)' : 'var(--text-tertiary)'}
                                                cursor="pointer"
                                                onClick={() => toggleRole(role)}
                                                _hover={{ borderColor: 'var(--accent)' }}
                                            >
                                                {form.roles.includes(role)
                                                    ? `${form.roles.indexOf(role) + 1}. ${role}`
                                                    : role}
                                            </Box>
                                        ))}
                                    </HStack>
                                </Box>

                                <HStack gap={2} justify="flex-end">
                                    <Box
                                        as="button"
                                        px={4} py={1.5}
                                        borderRadius="md"
                                        fontSize="sm"
                                        bg="transparent"
                                        color="var(--text-secondary)"
                                        cursor="pointer"
                                        onClick={() => setShowForm(false)}
                                    >
                                        Cancel
                                    </Box>
                                    <Box
                                        as="button"
                                        px={4} py={1.5}
                                        borderRadius="md"
                                        fontSize="sm"
                                        fontWeight="medium"
                                        bg="var(--accent)"
                                        color="var(--text-inverse)"
                                        cursor="pointer"
                                        onClick={handleSubmit}
                                        _hover={{ bg: 'var(--accent-hover)' }}
                                    >
                                        {busy ? <Spinner size="xs" /> : editingName ? 'Save' : 'Add'}
                                    </Box>
                                </HStack>
                            </VStack>
                        </Box>
                    )}
                </Box>

                {/* Queue */}
                {status && (
                    <VStack gap={4} mb={8} align="stretch">
                        <Box p={4} bg="var(--bg-card)" borderRadius="lg" border="1px solid" borderColor="var(--border-primary)">
                            <Text fontWeight="semibold" color="var(--text-heading)" mb={3}>Queue</Text>
                            <HStack gap={4} flexWrap="wrap">
                                {Object.entries(status.queue).map(([name, count]) => (
                                    <VStack key={name} gap={0} align="center" minW="60px">
                                        <Text fontSize="2xl" fontWeight="bold" color="var(--text-heading)">{count}</Text>
                                        <Text fontSize="xs" color="var(--text-tertiary)">{name}</Text>
                                    </VStack>
                                ))}
                            </HStack>
                        </Box>
                    </VStack>
                )}

                {/* Events */}
                <Heading size="md" color="var(--text-heading)" mb={4}>Events</Heading>
                <VStack gap={2} align="stretch">
                    {events.length === 0 ? (
                        <Text color="var(--text-muted)" textAlign="center" py={8}>No events yet</Text>
                    ) : (
                        events.map((ev, i) => (
                            <Box
                                key={i} p={3} bg="var(--bg-card)" borderRadius="md"
                                border="1px solid" borderColor="var(--border-primary)"
                            >
                                <HStack justify="space-between" mb={1}>
                                    <HStack gap={2}>
                                        <Badge colorScheme={EVENT_COLORS[ev.kind] || 'gray'} fontSize="xs">
                                            {ev.kind}
                                        </Badge>
                                        <Text fontSize="xs" color="var(--text-tertiary)">from {ev.source}</Text>
                                    </HStack>
                                    <Text fontSize="xs" color="var(--text-muted)">
                                        {new Date(ev.timestamp).toLocaleString()}
                                    </Text>
                                </HStack>
                                {ev.data.summary && (
                                    <Text fontSize="sm" color="var(--text-secondary)" mt={1}>{ev.data.summary}</Text>
                                )}
                            </Box>
                        ))
                    )}
                </VStack>
            </Box>
        </Box>
    );
};

export default StatusPage;
