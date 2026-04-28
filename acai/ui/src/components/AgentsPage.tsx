import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Box, VStack, HStack, Text, Heading, Badge, IconButton,
} from '@chakra-ui/react';
import {
    listAgents, deleteAgent, resetAgent,
    getAgentTemplate, listProviders,
    listToolNamespaces, listSkills,
} from '../services/api';
import type { ToolNamespace, SkillSummary } from '../services/api';
import type { AgentDef, Provider } from '../services/types';
import AgentEditModal, {
    emptyForm, agentDefToForm, DEFAULT_TEMPLATE,
    type AgentFormData,
} from './AgentEditModal';

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

const ResetIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="1 4 1 10 7 10" />
        <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
    </svg>
);

const ROLE_COLORS: Record<string, string> = {
    worker: 'blue',
    curator: 'purple',
    manager: 'orange',
};

/* ─── Main Page ────────────────────────────────────────────────── */

const AgentsPage = () => {
    const { agentName: urlAgent } = useParams<{ agentName?: string }>();
    const navigate = useNavigate();

    const [agents, setAgents] = useState<AgentDef[]>([]);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [toolNamespaces, setToolNamespaces] = useState<ToolNamespace[]>([]);
    const [skills, setSkills] = useState<SkillSummary[]>([]);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    const [modalOpen, setModalOpen] = useState(false);
    const [editingName, setEditingName] = useState<string | null>(null);
    const [modalForm, setModalForm] = useState<AgentFormData>(emptyForm);
    const [modalTemplate, setModalTemplate] = useState(DEFAULT_TEMPLATE);

    const urlHandled = useRef<string | undefined>(undefined);

    const refresh = useCallback(() => {
        listAgents().then(setAgents).catch(err => setError(err instanceof Error ? err.message : 'Failed to load'));
    }, []);

    useEffect(() => {
        document.title = 'Agents - Açaí';
        refresh();
        listProviders().then(setProviders).catch(() => {});
        listToolNamespaces().then(setToolNamespaces).catch(() => {});
        listSkills().then(setSkills).catch(() => {});
    }, [refresh]);

    useEffect(() => {
        if (!urlAgent || agents.length === 0 || urlHandled.current === urlAgent) return;
        const agent = agents.find(a => a.name === urlAgent);
        if (agent) {
            urlHandled.current = urlAgent;
            openEdit(agent);
        }
    }, [urlAgent, agents]);

    const openAdd = () => {
        setModalForm(emptyForm);
        setEditingName(null);
        setModalTemplate(DEFAULT_TEMPLATE);
        setModalOpen(true);
        navigate('/agents/new', { replace: true });
    };

    const openEdit = async (a: AgentDef) => {
        setModalForm(agentDefToForm(a));
        setEditingName(a.name);

        try {
            const { content } = await getAgentTemplate(a.name);
            setModalTemplate(content);
        } catch {
            setModalTemplate('');
        }

        setModalOpen(true);
        navigate(`/agents/${encodeURIComponent(a.name)}`, { replace: true });
    };

    const closeModal = useCallback(() => {
        setModalOpen(false);
        urlHandled.current = undefined;
        navigate('/agents', { replace: true });
    }, [navigate]);

    const handleDelete = async (name: string) => {
        setBusy(true);
        try {
            await deleteAgent(name);
            refresh();
        } catch { /* ignore */ } finally { setBusy(false); }
    };

    const handleReset = async (name: string) => {
        setBusy(true);
        try {
            await resetAgent(name);
            refresh();
        } catch { /* ignore */ } finally { setBusy(false); }
    };

    const handleModalSave = () => {
        closeModal();
        refresh();
    };

    return (
        <Box h="100vh" w="100%" bg="var(--bg-page)" overflowY="auto" p={6}>
            <Box maxW="4xl" mx="auto">
                <HStack justify="space-between" mb={6}>
                    <Heading size="lg" color="var(--text-heading)">Agents</Heading>
                    <IconButton
                        aria-label="Add agent"
                        size="sm"
                        variant="outline"
                        onClick={openAdd}
                        borderColor="var(--border-primary)"
                        color="var(--text-primary)"
                    >
                        <PlusIcon />
                    </IconButton>
                </HStack>

                {error && (
                    <Box p={3} bg="var(--bg-error)" borderRadius="md" mb={4}>
                        <Text color="var(--text-error)" fontSize="sm">{error}</Text>
                    </Box>
                )}

                {agents.length === 0 && (
                    <Text color="var(--text-muted)" fontSize="sm" py={8} textAlign="center">
                        No agents configured. Click (+) to create one.
                    </Text>
                )}

                <VStack gap={3} align="stretch" mb={6}>
                    {agents.map(a => (
                        <Box
                            key={a.name}
                            p={4}
                            bg="var(--bg-card)"
                            borderRadius="lg"
                            border="1px solid"
                            borderColor="var(--border-primary)"
                            cursor="pointer"
                            _hover={{ borderColor: 'var(--border-secondary)', bg: 'var(--bg-hover)' }}
                            transition="all 0.15s"
                            onClick={() => openEdit(a)}
                        >
                            <HStack justify="space-between" mb={2}>
                                <HStack gap={2}>
                                    {a.avatar && <Text fontSize="lg">{a.avatar}</Text>}
                                    <Text fontWeight="bold" color="var(--text-heading)" fontSize="sm">
                                        {a.name}
                                    </Text>
                                    <Badge colorScheme={ROLE_COLORS[a.role] || 'gray'} fontSize="2xs">
                                        {a.role}
                                    </Badge>
                                    <Badge variant="outline" fontSize="2xs">
                                        {a.provider === 'auto' ? 'auto' : a.provider}
                                    </Badge>
                                    {a.builtin && (
                                        <Badge colorScheme="green" fontSize="2xs" variant="subtle">
                                            built-in
                                        </Badge>
                                    )}
                                </HStack>
                                <HStack gap={1} onClick={e => e.stopPropagation()}>
                                    <IconButton
                                        aria-label="Edit" size="xs" variant="ghost"
                                        onClick={() => openEdit(a)}
                                        color="var(--text-tertiary)"
                                    >
                                        <EditIcon />
                                    </IconButton>
                                    {a.builtin && (
                                        <IconButton
                                            aria-label="Reset" size="xs" variant="ghost"
                                            onClick={() => handleReset(a.name)}
                                            color="var(--text-tertiary)" disabled={busy}
                                        >
                                            <ResetIcon />
                                        </IconButton>
                                    )}
                                    {!a.builtin && (
                                        <IconButton
                                            aria-label="Delete" size="xs" variant="ghost"
                                            onClick={() => handleDelete(a.name)}
                                            color="var(--text-error)" disabled={busy}
                                        >
                                            <TrashIcon />
                                        </IconButton>
                                    )}
                                </HStack>
                            </HStack>

                            {a.description && (
                                <Text fontSize="sm" color="var(--text-secondary)" mb={2}>
                                    {a.description}
                                </Text>
                            )}

                            <HStack gap={6} flexWrap="wrap">
                                <VStack align="flex-start" gap={0}>
                                    <Text fontSize="xs" color="var(--text-muted)">Max Iterations</Text>
                                    <Text fontSize="sm" color="var(--text-primary)">{a.max_iterations}</Text>
                                </VStack>
                                <VStack align="flex-start" gap={0}>
                                    <Text fontSize="xs" color="var(--text-muted)">Format</Text>
                                    <Text fontSize="sm" color="var(--text-primary)">
                                        {a.output_format || 'messages'}
                                    </Text>
                                </VStack>
                                <VStack align="flex-start" gap={0}>
                                    <Text fontSize="xs" color="var(--text-muted)">Approval</Text>
                                    <Text fontSize="sm" color="var(--text-primary)">
                                        {a.approval_required ? 'Required' : 'No'}
                                    </Text>
                                </VStack>
                                {a.uses_sandbox && (
                                    <VStack align="flex-start" gap={0}>
                                        <Text fontSize="xs" color="var(--text-muted)">Sandbox</Text>
                                        <Text fontSize="sm" color="var(--text-primary)">Enabled</Text>
                                    </VStack>
                                )}
                            </HStack>

                            {a.tools.length > 0 && (
                                <HStack mt={2} gap={1} flexWrap="wrap">
                                    <Text fontSize="xs" color="var(--text-muted)">Tools:</Text>
                                    {a.tools.map(t => (
                                        <Badge key={t} fontSize="2xs" variant="outline">{t}</Badge>
                                    ))}
                                </HStack>
                            )}

                            {a.tags.length > 0 && (
                                <HStack mt={1} gap={1} flexWrap="wrap">
                                    <Text fontSize="xs" color="var(--text-muted)">Tags:</Text>
                                    {a.tags.map(t => (
                                        <Badge key={t} fontSize="2xs" colorScheme="teal">{t}</Badge>
                                    ))}
                                </HStack>
                            )}
                        </Box>
                    ))}
                </VStack>

                {/* Edit/Create Modal */}
                {modalOpen && (
                    <AgentEditModal
                        editingName={editingName}
                        initialForm={modalForm}
                        initialTemplate={modalTemplate}
                        providers={providers}
                        toolNamespaces={toolNamespaces}
                        skills={skills}
                        onSave={handleModalSave}
                        onClose={closeModal}
                    />
                )}
            </Box>
        </Box>
    );
};

export default AgentsPage;
