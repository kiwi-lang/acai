import { useState, useEffect, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Box, VStack, HStack, Text, Heading, Badge, IconButton,
    Spinner, Button,
} from '@chakra-ui/react';
import {
    listAgents, deleteAgent, resetAgent,
    getAgentTemplate, listProviders,
    listToolNamespaces, listSkills,
    createAgent, updateAgent, updateAgentTemplate,
} from '../services/api';
import type { ToolNamespace, SkillSummary } from '../services/api';
import type { AgentDef, Provider } from '../services/types';
import {
    emptyForm, agentDefToForm, formToPayload, DEFAULT_TEMPLATE,
    AgentFormBody,
    type AgentFormData,
} from './AgentEditModal';

/* ─── Icons ─────────────────────────────────────────────────────── */

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

/* ─── Agent Detail Panel ───────────────────────────────────────── */

interface AgentDetailProps {
    editingName: string | null;
    form: AgentFormData;
    setForm: (fn: (prev: AgentFormData) => AgentFormData) => void;
    templateContent: string;
    setTemplateContent: (v: string) => void;
    templateDirty: boolean;
    setTemplateDirty: (v: boolean) => void;
    providers: Provider[];
    toolNamespaces: ToolNamespace[];
    skills: SkillSummary[];
    onSave: () => void;
    onDelete?: () => void;
    onReset?: () => void;
    busy: boolean;
    isBuiltin: boolean;
}

const AgentDetail = ({
    editingName, form, setForm,
    templateContent, setTemplateContent,
    templateDirty, setTemplateDirty,
    providers, toolNamespaces, skills,
    onSave, onDelete, onReset,
    busy, isBuiltin,
}: AgentDetailProps) => {
    const [formError, setFormError] = useState('');

    const handleSubmit = async () => {
        if (!form.name.trim()) { setFormError('Name is required'); return; }
        setFormError('');
        try {
            const payload = formToPayload(form) as Partial<AgentDef>;
            if (editingName) {
                await updateAgent(editingName, payload);
                if (templateDirty) {
                    await updateAgentTemplate(editingName, templateContent);
                }
            } else {
                await createAgent(payload);
                const slug = (payload.name ?? '').replace(/\s+/g, '-').toLowerCase();
                await updateAgentTemplate(slug, templateContent);
            }
            onSave();
        } catch (err) {
            setFormError(err instanceof Error ? err.message : 'Failed');
        }
    };

    const handleSaveTemplate = editingName
        ? async () => { await updateAgentTemplate(editingName, templateContent); }
        : undefined;

    return (
        <Box h="100%" display="flex" flexDirection="column">
            {/* Header */}
            <HStack px={5} py={4} borderBottom="1px solid" borderColor="var(--border-primary)"
                justify="space-between" flexShrink={0}>
                <HStack gap={2}>
                    <Heading size="sm" color="var(--text-heading)">
                        {editingName || 'New Agent'}
                    </Heading>
                    {isBuiltin && (
                        <Badge colorScheme="green" fontSize="2xs" variant="subtle">built-in</Badge>
                    )}
                </HStack>
                <HStack gap={1}>
                    {isBuiltin && onReset && (
                        <Button size="xs" variant="ghost" color="var(--text-tertiary)"
                            onClick={onReset} disabled={busy}
                            _hover={{ color: 'var(--text-heading)' }}>
                            Reset
                        </Button>
                    )}
                    {!isBuiltin && onDelete && (
                        <Button size="xs" variant="ghost" color="var(--text-error)"
                            onClick={onDelete} disabled={busy}>
                            Delete
                        </Button>
                    )}
                </HStack>
            </HStack>

            <AgentFormBody
                form={form} setForm={setForm}
                templateContent={templateContent} setTemplateContent={setTemplateContent}
                templateDirty={templateDirty} setTemplateDirty={setTemplateDirty}
                providers={providers} toolNamespaces={toolNamespaces} skills={skills}
                editingName={editingName}
                formError={formError}
                onSaveTemplate={handleSaveTemplate}
            />

            {/* Footer */}
            <HStack px={5} py={3} borderTop="1px solid" borderColor="var(--border-primary)"
                justify="flex-end" gap={2} flexShrink={0}>
                <Button size="sm" colorScheme="green" onClick={handleSubmit} disabled={busy}>
                    {busy ? <Spinner size="xs" /> : editingName ? 'Save' : 'Create'}
                </Button>
            </HStack>
        </Box>
    );
};

/* ─── Main Page ─────────────────────────────────────────────────── */

const AgentsPage = () => {
    const { agentName: urlAgent } = useParams<{ agentName?: string }>();
    const navigate = useNavigate();

    const [agents, setAgents] = useState<AgentDef[]>([]);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [toolNamespaces, setToolNamespaces] = useState<ToolNamespace[]>([]);
    const [skills, setSkills] = useState<SkillSummary[]>([]);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    const [selectedName, setSelectedName] = useState<string | null>(null);
    const [isCreating, setIsCreating] = useState(false);
    const [form, setForm] = useState<AgentFormData>(emptyForm);
    const [templateContent, setTemplateContent] = useState(DEFAULT_TEMPLATE);
    const [templateDirty, setTemplateDirty] = useState(false);

    const selectedAgent = useMemo(
        () => agents.find(a => a.name === selectedName) ?? null,
        [agents, selectedName],
    );

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
        if (urlAgent === 'new') {
            openAdd();
        } else if (urlAgent && agents.length > 0) {
            const agent = agents.find(a => a.name === urlAgent);
            if (agent && selectedName !== urlAgent) {
                selectAgent(agent);
            }
        }
    }, [urlAgent, agents]);

    const selectAgent = async (a: AgentDef) => {
        setIsCreating(false);
        setSelectedName(a.name);
        setForm(agentDefToForm(a));
        setTemplateDirty(false);

        try {
            const { content } = await getAgentTemplate(a.name);
            setTemplateContent(content);
        } catch {
            setTemplateContent('');
        }

        navigate(`/agents/${encodeURIComponent(a.name)}`, { replace: true });
    };

    const openAdd = () => {
        setSelectedName(null);
        setIsCreating(true);
        setForm(emptyForm);
        setTemplateContent(DEFAULT_TEMPLATE);
        setTemplateDirty(false);
        navigate('/agents/new', { replace: true });
    };

    const handleSave = () => {
        refresh();
        if (isCreating && form.name.trim()) {
            const slug = form.name.trim().replace(/\s+/g, '-').toLowerCase();
            setIsCreating(false);
            setSelectedName(slug);
            navigate(`/agents/${encodeURIComponent(slug)}`, { replace: true });
        }
    };

    const handleDelete = async () => {
        if (!selectedName) return;
        setBusy(true);
        try {
            await deleteAgent(selectedName);
            setSelectedName(null);
            setIsCreating(false);
            navigate('/agents', { replace: true });
            refresh();
        } catch { /* ignore */ } finally { setBusy(false); }
    };

    const handleReset = async () => {
        if (!selectedName) return;
        setBusy(true);
        try {
            await resetAgent(selectedName);
            refresh();
            const agent = agents.find(a => a.name === selectedName);
            if (agent) selectAgent(agent);
        } catch { /* ignore */ } finally { setBusy(false); }
    };

    const showDetail = isCreating || selectedName !== null;

    return (
        <Box h="100vh" w="100%" bg="var(--bg-page)" display="flex">
            {/* ─── Side Panel: Agent List ─── */}
            <Box
                w="300px" minW="300px"
                h="100%" bg="var(--bg-page)"
                borderRight="1px solid" borderColor="var(--border-primary)"
                display="flex" flexDirection="column"
            >
                <HStack px={4} py={4} justify="space-between" flexShrink={0}
                    borderBottom="1px solid" borderColor="var(--border-primary)">
                    <Heading size="md" color="var(--text-heading)">Agents</Heading>
                    <IconButton
                        aria-label="Add agent" size="sm" variant="outline"
                        onClick={openAdd}
                        borderColor="var(--border-primary)" color="var(--text-primary)"
                    >
                        <PlusIcon />
                    </IconButton>
                </HStack>

                <Box flex={1} overflowY="auto">
                    {error && (
                        <Box p={3} mx={3} mt={3} bg="var(--bg-error)" borderRadius="md">
                            <Text color="var(--text-error)" fontSize="xs">{error}</Text>
                        </Box>
                    )}

                    {agents.length === 0 && (
                        <Text color="var(--text-muted)" fontSize="sm" py={8} textAlign="center">
                            No agents configured.
                        </Text>
                    )}

                    <VStack gap={0} align="stretch">
                        {agents.map(a => {
                            const isSelected = selectedName === a.name && !isCreating;
                            return (
                                <Box
                                    key={a.name}
                                    px={4} py={3}
                                    cursor="pointer"
                                    bg={isSelected ? 'var(--bg-card-hover, var(--bg-hover))' : 'transparent'}
                                    borderLeft="3px solid"
                                    borderColor={isSelected ? 'var(--accent)' : 'transparent'}
                                    _hover={{ bg: 'var(--bg-card-hover, var(--bg-hover))' }}
                                    transition="all 0.1s"
                                    onClick={() => selectAgent(a)}
                                >
                                    <HStack justify="space-between" mb={1}>
                                        <HStack gap={2} minW={0}>
                                            {a.avatar && <Text fontSize="sm">{a.avatar}</Text>}
                                            <Text fontWeight="bold" color="var(--text-heading)"
                                                fontSize="sm" truncate>
                                                {a.name}
                                            </Text>
                                        </HStack>
                                        <HStack gap={1} flexShrink={0} onClick={e => e.stopPropagation()}>
                                            {a.builtin ? (
                                                <IconButton
                                                    aria-label="Reset" size="xs" variant="ghost"
                                                    onClick={() => { setSelectedName(a.name); handleReset(); }}
                                                    color="var(--text-tertiary)"
                                                >
                                                    <ResetIcon />
                                                </IconButton>
                                            ) : (
                                                <IconButton
                                                    aria-label="Delete" size="xs" variant="ghost"
                                                    onClick={() => { setSelectedName(a.name); handleDelete(); }}
                                                    color="var(--text-error)" disabled={busy}
                                                >
                                                    <TrashIcon />
                                                </IconButton>
                                            )}
                                        </HStack>
                                    </HStack>
                                    <HStack gap={2}>
                                        <Badge colorScheme={ROLE_COLORS[a.role] || 'gray'} fontSize="2xs">
                                            {a.role}
                                        </Badge>
                                        {a.builtin && (
                                            <Badge colorScheme="green" fontSize="2xs" variant="subtle">
                                                built-in
                                            </Badge>
                                        )}
                                    </HStack>
                                    {a.description && (
                                        <Text fontSize="xs" color="var(--text-muted)" mt={1} truncate>
                                            {a.description}
                                        </Text>
                                    )}
                                </Box>
                            );
                        })}
                    </VStack>
                </Box>
            </Box>

            {/* ─── Detail Panel ─── */}
            <Box flex={1} h="100%" overflow="hidden">
                {showDetail ? (
                    <AgentDetail
                        key={isCreating ? '__new__' : selectedName}
                        editingName={isCreating ? null : selectedName}
                        form={form}
                        setForm={setForm}
                        templateContent={templateContent}
                        setTemplateContent={setTemplateContent}
                        templateDirty={templateDirty}
                        setTemplateDirty={setTemplateDirty}
                        providers={providers}
                        toolNamespaces={toolNamespaces}
                        skills={skills}
                        onSave={handleSave}
                        onDelete={selectedAgent && !selectedAgent.builtin ? handleDelete : undefined}
                        onReset={selectedAgent?.builtin ? handleReset : undefined}
                        busy={busy}
                        isBuiltin={selectedAgent?.builtin ?? false}
                    />
                ) : (
                    <Box h="100%" display="flex" alignItems="center" justifyContent="center">
                        <VStack gap={2}>
                            <Text fontSize="lg" color="var(--text-muted)">Select an agent</Text>
                            <Text fontSize="sm" color="var(--text-tertiary)">
                                or click (+) to create one
                            </Text>
                        </VStack>
                    </Box>
                )}
            </Box>
        </Box>
    );
};

export default AgentsPage;
