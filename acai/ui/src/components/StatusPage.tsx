import { useState, useEffect, useCallback } from 'react';
import {
    Box, VStack, HStack, Text, Heading, Badge, IconButton, Input,
    NativeSelect, Spinner,
} from '@chakra-ui/react';
import {
    getStatus, listEvents, listProviders, createProvider,
    updateProvider, deleteProvider, activateProvider, fetchProviderModels,
    listModelSets, createModelSet, updateModelSet, deleteModelSet, setDefaultModelSet,
} from '../services/api';
import type { ModelSet, ModelSetEntry } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { AgentEvent, AgentStatus, ModelConfig, Provider } from '../services/types';
import DevServices from './DevServices';

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

const BACKENDS = ['vllm', 'openai', 'anthropic', 'google', 'llamacpp'];

const PROVIDER_TEMPLATES: Record<string, { backend: string; endpoint: string }> = {
    OpenAI: { backend: 'openai', endpoint: 'https://api.openai.com' },
    Anthropic: { backend: 'anthropic', endpoint: 'https://api.anthropic.com' },
    Google: { backend: 'google', endpoint: 'https://generativelanguage.googleapis.com' },
    Groq: { backend: 'openai', endpoint: 'https://api.groq.com/openai' },
    Mistral: { backend: 'openai', endpoint: 'https://api.mistral.ai' },
    'Local vLLM': { backend: 'vllm', endpoint: 'http://127.0.0.1:9123' },
};

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
    endpoint: string;
    api_key: string;
    server_port: string;
    max_tokens: string;
    temperature: string;
    context_window: string;
    priority: string;
    models: ModelConfig[];
}

const emptyModel: ModelConfig = { name: '', slug: '', max_tokens: 0, context_window: 0, cost_weight: 10, smart_weight: 10 };

const emptyForm: ProviderFormData = {
    name: '', backend: 'openai', endpoint: '',
    api_key: '', server_port: '9123', max_tokens: '4096',
    temperature: '1.0', context_window: '128000', priority: '50', models: [],
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

    const [fetchingModels, setFetchingModels] = useState(false);
    const [showAllModels, setShowAllModels] = useState(false);
    const MODEL_DISPLAY_LIMIT = 11;

    const openAdd = () => {
        setForm(emptyForm);
        setEditingName(null);
        setFormError('');
        setShowAllModels(false);
        setShowForm(true);
    };

    const applyTemplate = (tplName: string) => {
        const tpl = PROVIDER_TEMPLATES[tplName];
        if (!tpl) return;
        setForm(prev => ({
            ...prev,
            name: prev.name || tplName,
            backend: tpl.backend,
            endpoint: tpl.endpoint,
        }));
    };

    const openEdit = (p: Provider) => {
        setForm({
            name: p.name,
            backend: p.backend,
            endpoint: p.endpoint,
            api_key: p.api_key,
            server_port: String(p.server_port),
            max_tokens: String(p.max_tokens),
            temperature: String(p.temperature),
            context_window: String(p.context_window ?? 128000),
            priority: String(p.priority),
            models: p.models?.length ? p.models.map(m => ({ ...m })) : [],
        });
        setEditingName(p.name);
        setFormError('');
        setShowAllModels(false);
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
                endpoint: form.endpoint,
                api_key: form.api_key,
                server_port: parseInt(form.server_port) || 9123,
                max_tokens: parseInt(form.max_tokens) || 4096,
                temperature: parseFloat(form.temperature) || 1.0,
                context_window: parseInt(form.context_window) || 128000,
                priority: parseInt(form.priority) || 0,
                models: form.models,
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

    const addModel = () => {
        setForm(prev => ({ ...prev, models: [...prev.models, { ...emptyModel }] }));
    };

    const removeModel = (idx: number) => {
        setForm(prev => ({ ...prev, models: prev.models.filter((_, i) => i !== idx) }));
    };

    const updateModel = (idx: number, key: keyof ModelConfig, value: string | number) => {
        setForm(prev => {
            const models = prev.models.map((m, i) => i === idx ? { ...m, [key]: value } : m);
            return { ...prev, models };
        });
    };

    const setDefaultModel = (idx: number) => {
        setForm(prev => {
            if (idx === 0 || idx >= prev.models.length) return prev;
            const models = [...prev.models];
            const [target] = models.splice(idx, 1);
            models.unshift(target);
            return { ...prev, models };
        });
    };

    const handleFetchModels = async () => {
        const provName = editingName || form.name.trim();
        if (!provName) { setFormError('Save provider first to fetch models'); return; }
        setFetchingModels(true);
        setFormError('');
        try {
            const fetched = await fetchProviderModels(provName);
            setForm(prev => ({ ...prev, models: fetched }));
        } catch (err) {
            setFormError(err instanceof Error ? err.message : 'Failed to fetch models');
        } finally {
            setFetchingModels(false);
        }
    };

    const setField = (key: keyof ProviderFormData, value: string) =>
        setForm(prev => ({ ...prev, [key]: value }));

    // ------------------------------------------------------------------
    // Model Sets state and handlers
    // ------------------------------------------------------------------
    const [modelSets, setModelSets] = useState<ModelSet[]>([]);
    const [showMsForm, setShowMsForm] = useState(false);
    const [editingMsName, setEditingMsName] = useState<string | null>(null);
    const [msForm, setMsForm] = useState<ModelSet>({ name: '', default: false, entries: [] });
    const [msFormError, setMsFormError] = useState('');
    const [msBusy, setMsBusy] = useState(false);

    const COMPLEXITY_LEVELS = ['low', 'medium', 'high'];

    const refreshModelSets = useCallback(() => {
        listModelSets().then(setModelSets).catch(() => {});
    }, []);

    useEffect(() => { refreshModelSets(); }, [refreshModelSets]);

    const openMsAdd = () => {
        setMsForm({ name: '', default: modelSets.length === 0, entries: [] });
        setEditingMsName(null);
        setMsFormError('');
        setShowMsForm(true);
    };

    const openMsEdit = (ms: ModelSet) => {
        setMsForm({ ...ms, entries: ms.entries.map(e => ({ ...e })) });
        setEditingMsName(ms.name);
        setMsFormError('');
        setShowMsForm(true);
    };

    const handleMsSubmit = async () => {
        if (!msForm.name.trim()) { setMsFormError('Name is required'); return; }
        setMsBusy(true);
        setMsFormError('');
        try {
            if (editingMsName) {
                await updateModelSet(editingMsName, msForm);
            } else {
                await createModelSet(msForm);
            }
            setShowMsForm(false);
            refreshModelSets();
        } catch (err) {
            setMsFormError(err instanceof Error ? err.message : 'Failed');
        } finally {
            setMsBusy(false);
        }
    };

    const handleMsDelete = async (name: string) => {
        setMsBusy(true);
        try { await deleteModelSet(name); refreshModelSets(); }
        catch { /* ignore */ } finally { setMsBusy(false); }
    };

    const handleMsDefault = async (name: string) => {
        setMsBusy(true);
        try { await setDefaultModelSet(name); refreshModelSets(); }
        catch { /* ignore */ } finally { setMsBusy(false); }
    };

    const addMsEntry = () => {
        setMsForm(prev => ({
            ...prev,
            entries: [...prev.entries, { provider: '', model: '', price_input: 0, price_output: 0, complexity_min: 'low' }],
        }));
    };

    const removeMsEntry = (idx: number) => {
        setMsForm(prev => ({ ...prev, entries: prev.entries.filter((_, i) => i !== idx) }));
    };

    const updateMsEntry = (idx: number, key: keyof ModelSetEntry, value: string | number) => {
        setMsForm(prev => ({
            ...prev,
            entries: prev.entries.map((e, i) => i === idx ? { ...e, [key]: value } : e),
        }));
    };

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
                                            <Badge colorScheme="green" fontSize="2xs">default</Badge>
                                        )}
                                        <Badge variant="outline" fontSize="2xs">{p.backend}</Badge>
                                    </HStack>
                                    <HStack gap={1}>
                                        {!p.active && (
                                            <IconButton
                                                aria-label="Set default"
                                                size="xs"
                                                variant="ghost"
                                                onClick={() => handleActivate(p.name)}
                                                color="var(--accent)"
                                                title="Set as default provider"
                                                disabled={busy}
                                            >
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
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
                                        <Text fontSize="xs" color="var(--text-muted)">Models</Text>
                                        <HStack gap={1} flexWrap="wrap">
                                            {(p.models || []).length > 0
                                                ? <>
                                                    {p.models.slice(0, 11).map((m, i) => (
                                                        <Badge key={m.slug || i} fontSize="2xs" variant={i === 0 ? 'solid' : 'outline'}
                                                            colorPalette={i === 0 ? 'green' : 'gray'}>
                                                            {m.name || m.slug}
                                                        </Badge>
                                                    ))}
                                                    {p.models.length > 11 && (
                                                        <Text fontSize="2xs" color="var(--text-muted)">
                                                            +{p.models.length - 11} more
                                                        </Text>
                                                    )}
                                                </>
                                                : <Text fontSize="sm" color="var(--text-muted)">—</Text>}
                                        </HStack>
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
                                {!editingName && (
                                    <Box>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Quick Template</Text>
                                        <NativeSelect.Root size="sm">
                                            <NativeSelect.Field
                                                value=""
                                                onChange={e => { if (e.target.value) applyTemplate(e.target.value); }}
                                                bg="var(--bg-input)" color="var(--text-primary)"
                                                borderColor="var(--border-input)"
                                            >
                                                <option value="" style={{ background: 'var(--option-bg)' }}>Select a template...</option>
                                                {Object.keys(PROVIDER_TEMPLATES).map(t => (
                                                    <option key={t} value={t} style={{ background: 'var(--option-bg)' }}>{t}</option>
                                                ))}
                                            </NativeSelect.Field>
                                        </NativeSelect.Root>
                                    </Box>
                                )}

                                <HStack gap={3}>
                                    <Box flex={1}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Name</Text>
                                        <Input
                                            size="sm" placeholder="e.g. OpenAI"
                                            value={form.name}
                                            onChange={e => setField('name', e.target.value)}
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
                                                    <option key={b} value={b} style={{ background: 'var(--option-bg)' }}>{b}</option>
                                                ))}
                                            </NativeSelect.Field>
                                        </NativeSelect.Root>
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
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Max Tokens (default)</Text>
                                        <Input
                                            size="sm" type="number" placeholder="4096"
                                            value={form.max_tokens}
                                            onChange={e => setField('max_tokens', e.target.value)}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        />
                                    </Box>
                                    <Box flex={1}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Context Window (default)</Text>
                                        <Input
                                            size="sm" type="number" placeholder="128000"
                                            value={form.context_window}
                                            onChange={e => setField('context_window', e.target.value)}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        />
                                    </Box>
                                    <Box flex={1}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Temperature</Text>
                                        <Input
                                            size="sm" type="number" step="0.1" placeholder="1.0"
                                            value={form.temperature}
                                            onChange={e => setField('temperature', e.target.value)}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        />
                                    </Box>
                                </HStack>

                                {/* Models sub-section */}
                                <Box>
                                    <HStack justify="space-between" mb={2}>
                                        <Text fontSize="xs" fontWeight="semibold" color="var(--text-heading)">
                                            Models {form.models.length > 0 && `(${form.models.length})`}
                                        </Text>
                                        <HStack gap={1}>
                                            {editingName && (
                                                <Box
                                                    as="button" px={2} py={0.5} borderRadius="md" fontSize="xs"
                                                    border="1px solid" borderColor="var(--border-primary)"
                                                    color="var(--text-tertiary)" cursor="pointer"
                                                    onClick={handleFetchModels}
                                                    _hover={{ borderColor: 'var(--accent)' }}
                                                >
                                                    {fetchingModels ? <Spinner size="xs" /> : 'Fetch Models'}
                                                </Box>
                                            )}
                                            <Box
                                                as="button" px={2} py={0.5} borderRadius="md" fontSize="xs"
                                                border="1px solid" borderColor="var(--border-primary)"
                                                color="var(--text-tertiary)" cursor="pointer"
                                                onClick={addModel}
                                                _hover={{ borderColor: 'var(--accent)' }}
                                            >
                                                + Add
                                            </Box>
                                        </HStack>
                                    </HStack>

                                    {form.models.length === 0 ? (
                                        <Text fontSize="xs" color="var(--text-muted)" py={2}>
                                            No models configured. Add manually or fetch from provider API.
                                        </Text>
                                    ) : (
                                        <VStack gap={2} align="stretch">
                                            {(showAllModels ? form.models : form.models.slice(0, MODEL_DISPLAY_LIMIT)).map((m, idx) => (
                                                <Box
                                                    key={idx} p={2} bg="var(--bg-card)" borderRadius="md"
                                                    border="1px solid"
                                                    borderColor={idx === 0 ? 'var(--accent)' : 'var(--border-primary)'}
                                                >
                                                    <HStack gap={2} mb={1}>
                                                        <Box flex={2}>
                                                            <Input
                                                                size="xs" placeholder="Display name"
                                                                value={m.name}
                                                                onChange={e => updateModel(idx, 'name', e.target.value)}
                                                                bg="var(--bg-input)" color="var(--text-primary)"
                                                                borderColor="var(--border-input)" fontSize="xs"
                                                            />
                                                        </Box>
                                                        <Box flex={2}>
                                                            <Input
                                                                size="xs" placeholder="API slug"
                                                                value={m.slug}
                                                                onChange={e => updateModel(idx, 'slug', e.target.value)}
                                                                bg="var(--bg-input)" color="var(--text-primary)"
                                                                borderColor="var(--border-input)" fontSize="xs" fontFamily="mono"
                                                            />
                                                        </Box>
                                                        <HStack gap={1}>
                                                            {idx !== 0 && (
                                                                <Box
                                                                    as="button" title="Set as default" cursor="pointer"
                                                                    color="var(--text-muted)" _hover={{ color: 'var(--accent)' }}
                                                                    onClick={() => setDefaultModel(idx)}
                                                                >
                                                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                                                                        <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
                                                                    </svg>
                                                                </Box>
                                                            )}
                                                            {idx === 0 && (
                                                                <Badge fontSize="2xs" colorPalette="green" variant="solid">default</Badge>
                                                            )}
                                                            <Box
                                                                as="button" cursor="pointer"
                                                                color="var(--text-error)" _hover={{ opacity: 0.8 }}
                                                                onClick={() => removeModel(idx)}
                                                            >
                                                                <TrashIcon />
                                                            </Box>
                                                        </HStack>
                                                    </HStack>
                                                    <HStack gap={2}>
                                                        <Box flex={1}>
                                                            <Input
                                                                size="xs" type="number"
                                                                placeholder={`max_tokens (${form.max_tokens})`}
                                                                value={m.max_tokens || ''}
                                                                onChange={e => updateModel(idx, 'max_tokens', parseInt(e.target.value) || 0)}
                                                                bg="var(--bg-input)" color="var(--text-primary)"
                                                                borderColor="var(--border-input)" fontSize="xs"
                                                            />
                                                        </Box>
                                                        <Box flex={1}>
                                                            <Input
                                                                size="xs" type="number"
                                                                placeholder={`ctx (${form.context_window})`}
                                                                value={m.context_window || ''}
                                                                onChange={e => updateModel(idx, 'context_window', parseInt(e.target.value) || 0)}
                                                                bg="var(--bg-input)" color="var(--text-primary)"
                                                                borderColor="var(--border-input)" fontSize="xs"
                                                            />
                                                        </Box>
                                                        <Box flex={1}>
                                                            <Input
                                                                size="xs" type="number"
                                                                placeholder="cost: 10"
                                                                value={m.cost_weight}
                                                                onChange={e => updateModel(idx, 'cost_weight', parseInt(e.target.value) || 10)}
                                                                bg="var(--bg-input)" color="var(--text-primary)"
                                                                borderColor="var(--border-input)" fontSize="xs"
                                                            />
                                                        </Box>
                                                        <Box flex={1}>
                                                            <Input
                                                                size="xs" type="number"
                                                                placeholder="smart: 10"
                                                                value={m.smart_weight}
                                                                onChange={e => updateModel(idx, 'smart_weight', parseInt(e.target.value) || 10)}
                                                                bg="var(--bg-input)" color="var(--text-primary)"
                                                                borderColor="var(--border-input)" fontSize="xs"
                                                            />
                                                        </Box>
                                                    </HStack>
                                                </Box>
                                            ))}
                                            {!showAllModels && form.models.length > MODEL_DISPLAY_LIMIT && (
                                                <Box
                                                    as="button" py={1} textAlign="center" cursor="pointer"
                                                    fontSize="xs" color="var(--accent)"
                                                    _hover={{ textDecoration: 'underline' }}
                                                    onClick={() => setShowAllModels(true)}
                                                >
                                                    Show all {form.models.length} models ({form.models.length - MODEL_DISPLAY_LIMIT} hidden)
                                                </Box>
                                            )}
                                            {showAllModels && form.models.length > MODEL_DISPLAY_LIMIT && (
                                                <Box
                                                    as="button" py={1} textAlign="center" cursor="pointer"
                                                    fontSize="xs" color="var(--text-muted)"
                                                    _hover={{ textDecoration: 'underline' }}
                                                    onClick={() => setShowAllModels(false)}
                                                >
                                                    Show fewer
                                                </Box>
                                            )}
                                        </VStack>
                                    )}
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

                {/* Model Sets */}
                <Box mb={8}>
                    <HStack justify="space-between" mb={3}>
                        <Text fontWeight="semibold" color="var(--text-heading)" fontSize="lg">
                            Model Sets
                        </Text>
                        <IconButton
                            aria-label="Add model set"
                            size="sm"
                            variant="outline"
                            onClick={openMsAdd}
                            borderColor="var(--border-primary)"
                            color="var(--text-primary)"
                        >
                            <PlusIcon />
                        </IconButton>
                    </HStack>

                    {modelSets.length === 0 && !showMsForm && (
                        <Text color="var(--text-muted)" fontSize="sm" py={4} textAlign="center">
                            No model sets configured. Create one to enable automatic model routing.
                        </Text>
                    )}

                    <VStack gap={3} align="stretch">
                        {modelSets.map(ms => (
                            <Box
                                key={ms.name}
                                p={4}
                                bg="var(--bg-card)"
                                borderRadius="lg"
                                border="2px solid"
                                borderColor={ms.default ? 'var(--accent)' : 'var(--border-primary)'}
                            >
                                <HStack justify="space-between" mb={2}>
                                    <HStack gap={2}>
                                        <Text fontWeight="bold" color="var(--text-heading)" fontSize="sm">
                                            {ms.name}
                                        </Text>
                                        {ms.default && (
                                            <Badge colorScheme="green" fontSize="2xs">default</Badge>
                                        )}
                                        <Badge variant="outline" fontSize="2xs">
                                            {ms.entries.length} model{ms.entries.length !== 1 ? 's' : ''}
                                        </Badge>
                                    </HStack>
                                    <HStack gap={1}>
                                        {!ms.default && (
                                            <IconButton
                                                aria-label="Set default"
                                                size="xs"
                                                variant="ghost"
                                                onClick={() => handleMsDefault(ms.name)}
                                                color="var(--accent)"
                                                title="Set as default model set"
                                                disabled={msBusy}
                                            >
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                                    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                                                </svg>
                                            </IconButton>
                                        )}
                                        <IconButton
                                            aria-label="Edit" size="xs" variant="ghost"
                                            onClick={() => openMsEdit(ms)}
                                            color="var(--text-tertiary)"
                                        >
                                            <EditIcon />
                                        </IconButton>
                                        <IconButton
                                            aria-label="Delete" size="xs" variant="ghost"
                                            onClick={() => handleMsDelete(ms.name)}
                                            color="var(--text-error)" disabled={msBusy}
                                        >
                                            <TrashIcon />
                                        </IconButton>
                                    </HStack>
                                </HStack>

                                {ms.entries.length > 0 && (
                                    <VStack gap={1} align="stretch">
                                        {ms.entries.map((entry, idx) => (
                                            <HStack key={idx} gap={3} fontSize="xs" color="var(--text-secondary)">
                                                <Text fontFamily="mono" minW="120px">{entry.provider}/{entry.model}</Text>
                                                <Badge variant="outline" fontSize="2xs">{entry.complexity_min}</Badge>
                                                <Text color="var(--text-muted)">
                                                    ${entry.price_input}/{entry.price_output} per Mtok
                                                </Text>
                                            </HStack>
                                        ))}
                                    </VStack>
                                )}
                            </Box>
                        ))}
                    </VStack>

                    {/* Model Set Add/Edit form */}
                    {showMsForm && (
                        <Box
                            mt={3} p={4} bg="var(--bg-elevated)" borderRadius="lg"
                            border="1px solid" borderColor="var(--border-primary)"
                        >
                            <Text fontWeight="semibold" color="var(--text-heading)" mb={3} fontSize="sm">
                                {editingMsName ? `Edit "${editingMsName}"` : 'New Model Set'}
                            </Text>

                            {msFormError && (
                                <Box p={2} bg="var(--bg-error)" borderRadius="md" mb={3}>
                                    <Text color="var(--text-error)" fontSize="xs">{msFormError}</Text>
                                </Box>
                            )}

                            <VStack gap={3} align="stretch">
                                <HStack gap={3}>
                                    <Box flex={1}>
                                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Name</Text>
                                        <Input
                                            size="sm" placeholder="e.g. default"
                                            value={msForm.name}
                                            onChange={e => setMsForm(prev => ({ ...prev, name: e.target.value }))}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        />
                                    </Box>
                                    <Box pt={5}>
                                        <HStack gap={2}>
                                            <input
                                                type="checkbox"
                                                checked={msForm.default}
                                                onChange={e => setMsForm(prev => ({ ...prev, default: e.target.checked }))}
                                            />
                                            <Text fontSize="xs" color="var(--text-muted)">Default</Text>
                                        </HStack>
                                    </Box>
                                </HStack>

                                {/* Entries */}
                                <Box>
                                    <HStack justify="space-between" mb={2}>
                                        <Text fontSize="xs" fontWeight="semibold" color="var(--text-heading)">
                                            Models {msForm.entries.length > 0 && `(${msForm.entries.length})`}
                                        </Text>
                                        <Box
                                            as="button" px={2} py={0.5} borderRadius="md" fontSize="xs"
                                            border="1px solid" borderColor="var(--border-primary)"
                                            color="var(--text-tertiary)" cursor="pointer"
                                            onClick={addMsEntry}
                                            _hover={{ borderColor: 'var(--accent)' }}
                                        >
                                            + Add Model
                                        </Box>
                                    </HStack>

                                    {msForm.entries.length === 0 ? (
                                        <Text fontSize="xs" color="var(--text-muted)" py={2}>
                                            No models in this set. Add provider:model entries.
                                        </Text>
                                    ) : (
                                        <VStack gap={2} align="stretch">
                                            {msForm.entries.map((entry, idx) => (
                                                <Box
                                                    key={idx} p={2} bg="var(--bg-card)" borderRadius="md"
                                                    border="1px solid" borderColor="var(--border-primary)"
                                                >
                                                    <HStack gap={2} mb={1}>
                                                        <Box flex={1}>
                                                            <Text fontSize="2xs" color="var(--text-muted)">Provider</Text>
                                                            <NativeSelect.Root size="sm">
                                                                <NativeSelect.Field
                                                                    value={entry.provider}
                                                                    onChange={e => updateMsEntry(idx, 'provider', e.target.value)}
                                                                    bg="var(--bg-input)" color="var(--text-primary)"
                                                                    borderColor="var(--border-input)" fontSize="xs"
                                                                >
                                                                    <option value="" style={{ background: 'var(--option-bg)' }}>Select...</option>
                                                                    {providers.map(p => (
                                                                        <option key={p.name} value={p.name} style={{ background: 'var(--option-bg)' }}>
                                                                            {p.name}
                                                                        </option>
                                                                    ))}
                                                                </NativeSelect.Field>
                                                            </NativeSelect.Root>
                                                        </Box>
                                                        <Box flex={1}>
                                                            <Text fontSize="2xs" color="var(--text-muted)">Model</Text>
                                                            <NativeSelect.Root size="sm">
                                                                <NativeSelect.Field
                                                                    value={entry.model}
                                                                    onChange={e => updateMsEntry(idx, 'model', e.target.value)}
                                                                    bg="var(--bg-input)" color="var(--text-primary)"
                                                                    borderColor="var(--border-input)" fontSize="xs"
                                                                >
                                                                    <option value="" style={{ background: 'var(--option-bg)' }}>Select...</option>
                                                                    {(providers.find(p => p.name === entry.provider)?.models || []).map(m => (
                                                                        <option key={m.slug} value={m.slug} style={{ background: 'var(--option-bg)' }}>
                                                                            {m.name || m.slug}
                                                                        </option>
                                                                    ))}
                                                                </NativeSelect.Field>
                                                            </NativeSelect.Root>
                                                        </Box>
                                                        <Box>
                                                            <Text fontSize="2xs" color="var(--text-muted)">Complexity</Text>
                                                            <NativeSelect.Root size="sm">
                                                                <NativeSelect.Field
                                                                    value={entry.complexity_min}
                                                                    onChange={e => updateMsEntry(idx, 'complexity_min', e.target.value)}
                                                                    bg="var(--bg-input)" color="var(--text-primary)"
                                                                    borderColor="var(--border-input)" fontSize="xs"
                                                                >
                                                                    {COMPLEXITY_LEVELS.map(l => (
                                                                        <option key={l} value={l} style={{ background: 'var(--option-bg)' }}>{l}</option>
                                                                    ))}
                                                                </NativeSelect.Field>
                                                            </NativeSelect.Root>
                                                        </Box>
                                                        <Box
                                                            as="button" cursor="pointer" pt={4}
                                                            color="var(--text-error)" _hover={{ opacity: 0.8 }}
                                                            onClick={() => removeMsEntry(idx)}
                                                        >
                                                            <TrashIcon />
                                                        </Box>
                                                    </HStack>
                                                    <HStack gap={2}>
                                                        <Box flex={1}>
                                                            <Text fontSize="2xs" color="var(--text-muted)">Input $/Mtok</Text>
                                                            <Input
                                                                size="xs" type="number" step="0.01" placeholder="0.00"
                                                                value={entry.price_input || ''}
                                                                onChange={e => updateMsEntry(idx, 'price_input', parseFloat(e.target.value) || 0)}
                                                                bg="var(--bg-input)" color="var(--text-primary)"
                                                                borderColor="var(--border-input)" fontSize="xs"
                                                            />
                                                        </Box>
                                                        <Box flex={1}>
                                                            <Text fontSize="2xs" color="var(--text-muted)">Output $/Mtok</Text>
                                                            <Input
                                                                size="xs" type="number" step="0.01" placeholder="0.00"
                                                                value={entry.price_output || ''}
                                                                onChange={e => updateMsEntry(idx, 'price_output', parseFloat(e.target.value) || 0)}
                                                                bg="var(--bg-input)" color="var(--text-primary)"
                                                                borderColor="var(--border-input)" fontSize="xs"
                                                            />
                                                        </Box>
                                                    </HStack>
                                                </Box>
                                            ))}
                                        </VStack>
                                    )}
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
                                        onClick={() => setShowMsForm(false)}
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
                                        onClick={handleMsSubmit}
                                        _hover={{ bg: 'var(--accent-hover)' }}
                                    >
                                        {msBusy ? <Spinner size="xs" /> : editingMsName ? 'Save' : 'Add'}
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

                {/* Dev Services */}
                <Box mb={6}>
                    <DevServices />
                </Box>

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
