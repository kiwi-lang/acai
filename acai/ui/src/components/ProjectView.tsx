import { useState, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Box, VStack, HStack, Text, Heading, Badge, IconButton, Spinner,
    Input, Button, NativeSelect, Textarea,
} from '@chakra-ui/react';
import { getProject, listTasks, createTask, getTaskTree, listAgents, updateProject, updateTask } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { Project, Task, AgentDef } from '../services/types';
import ChatPanel from './ChatPanel';
import Markdown from './Markdown';

const BackIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="15 18 9 12 15 6" />
    </svg>
);

const PencilIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
);

const CloseIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
);

/* ─── Field wrapper ──────────────────────────────────────────────── */
const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <Box>
        <Text fontSize="xs" fontWeight="medium" color="var(--text-tertiary)" mb={1}>{label}</Text>
        {children}
    </Box>
);

const inputStyle = {
    bg: 'var(--bg-input)',
    color: 'var(--text-heading)',
    borderColor: 'var(--border-secondary)',
    _focus: { borderColor: 'var(--accent)', boxShadow: 'none' },
    _placeholder: { color: 'var(--text-muted)' },
};

/* ─── Edit Modal ─────────────────────────────────────────────────── */
interface EditModalProps {
    project: Project;
    agents: AgentDef[];
    onSave: (patch: Partial<Project>) => Promise<void>;
    onClose: () => void;
}

const ProjectEditModal = ({ project, agents, onSave, onClose }: EditModalProps) => {
    const [form, setForm] = useState({
        language: project.language || 'python',
        template: project.template || 'default',
        repo_url: project.repo_url || '',
        provider: project.provider || '',
        path: project.path || '',
        python_version: project.python_version || '3.12',
        venv_path: project.venv_path || '.venv',
        refiner: project.refiner || 'refiner',
    });
    const [saving, setSaving] = useState(false);

    const set = (key: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
        setForm(prev => ({ ...prev, [key]: e.target.value }));

    const handleSave = async () => {
        setSaving(true);
        try { await onSave(form); }
        finally { setSaving(false); }
    };

    const handleBackdrop = (e: React.MouseEvent) => {
        if (e.target === e.currentTarget) onClose();
    };

    return createPortal(
        <Box
            position="fixed" inset={0} zIndex={1400}
            display="flex" alignItems="center" justifyContent="center"
            onClick={handleBackdrop}
        >
            <Box position="absolute" inset={0} bg="blackAlpha.600" />
            <Box
                position="relative" zIndex={1}
                bg="var(--bg-page)" borderRadius="xl"
                border="1px solid" borderColor="var(--border-primary)"
                boxShadow="xl" w="full" maxW="540px" mx={4}
                maxH="90vh" overflowY="auto"
            >
                {/* Header */}
                <HStack px={5} py={4} borderBottom="1px solid" borderColor="var(--border-primary)" justify="space-between">
                    <Heading size="sm" color="var(--text-heading)">Edit Project — {project.name}</Heading>
                    <IconButton aria-label="Close" variant="ghost" size="sm" color="var(--text-tertiary)"
                        _hover={{ color: 'var(--text-heading)' }} onClick={onClose}>
                        <CloseIcon />
                    </IconButton>
                </HStack>

                {/* Body */}
                <VStack px={5} py={4} gap={4} align="stretch">
                    <HStack gap={4}>
                        <Field label="Language">
                            <NativeSelect.Root size="sm">
                                <NativeSelect.Field value={form.language} onChange={set('language')} {...inputStyle}>
                                    {['python', 'typescript', 'javascript', 'rust', 'go', 'java', 'c++', 'other'].map(l => (
                                        <option key={l} value={l}>{l}</option>
                                    ))}
                                </NativeSelect.Field>
                            </NativeSelect.Root>
                        </Field>
                        <Field label="Template">
                            <NativeSelect.Root size="sm">
                                <NativeSelect.Field value={form.template} onChange={set('template')} {...inputStyle}>
                                    {['default', 'library', 'cli', 'web-api', 'minimal'].map(t => (
                                        <option key={t} value={t}>{t}</option>
                                    ))}
                                </NativeSelect.Field>
                            </NativeSelect.Root>
                        </Field>
                    </HStack>

                    <Field label="Path">
                        <Input size="sm" value={form.path} onChange={set('path')} fontFamily="mono" {...inputStyle} />
                    </Field>

                    <Field label="Repository URL">
                        <Input size="sm" value={form.repo_url} onChange={set('repo_url')} placeholder="git@github.com:..." {...inputStyle} />
                    </Field>

                    <HStack gap={4}>
                        <Field label="Python version">
                            <Input size="sm" value={form.python_version} onChange={set('python_version')} w="100px" {...inputStyle} />
                        </Field>
                        <Field label="Venv path">
                            <Input size="sm" value={form.venv_path} onChange={set('venv_path')} w="140px" fontFamily="mono" {...inputStyle} />
                        </Field>
                        <Field label="Provider">
                            <Input size="sm" value={form.provider} onChange={set('provider')} placeholder="github" {...inputStyle} />
                        </Field>
                    </HStack>

                    <Field label="Refiner agent (default for project chat)">
                        <NativeSelect.Root size="sm" maxW="280px">
                            <NativeSelect.Field value={form.refiner} onChange={set('refiner')} {...inputStyle}>
                                {(agents.length ? agents : [{ name: 'refiner' }]).map(a => (
                                    <option key={a.name} value={a.name}>{a.name}</option>
                                ))}
                            </NativeSelect.Field>
                        </NativeSelect.Root>
                    </Field>

                    {/* Read-only info */}
                    <HStack gap={4} pt={1}>
                        <Box>
                            <Text fontSize="2xs" color="var(--text-muted)">Source</Text>
                            <Badge colorScheme={project.source === 'clone' ? 'purple' : 'green'} fontSize="xs">{project.source}</Badge>
                        </Box>
                        <Box>
                            <Text fontSize="2xs" color="var(--text-muted)">Created</Text>
                            <Text fontSize="xs" color="var(--text-secondary)">{new Date(project.created_at).toLocaleString()}</Text>
                        </Box>
                    </HStack>
                </VStack>

                {/* Footer */}
                <HStack px={5} py={3} borderTop="1px solid" borderColor="var(--border-primary)" justify="flex-end" gap={2}>
                    <Button size="sm" variant="ghost" color="var(--text-tertiary)" onClick={onClose}>
                        Cancel
                    </Button>
                    <Button size="sm" colorScheme="green" onClick={handleSave} loading={saving} disabled={saving}>
                        Save
                    </Button>
                </HStack>
            </Box>
        </Box>,
        document.body,
    );
};

const STATUS_COLORS: Record<string, string> = {
    pending: 'yellow',
    curating: 'orange',
    ready: 'blue',
    in_progress: 'cyan',
    completed: 'green',
    failed: 'red',
    review: 'purple',
};

const COLUMNS = [
    { key: 'pending', label: 'Pending' },
    { key: 'ready', label: 'Ready' },
    { key: 'in_progress', label: 'In Progress' },
    { key: 'review', label: 'Review' },
    { key: 'completed', label: 'Done' },
    { key: 'failed', label: 'Failed' },
];

const KIND_LABELS: Record<string, { label: string; color: string }> = {
    task: { label: 'Task', color: 'green' },
    llm_complete: { label: 'LLM', color: 'teal' },
    tool_call: { label: 'Tool', color: 'orange' },
};

const KanbanCard = ({ task, onClick }: { task: Task; onClick?: () => void }) => {
    const kind = KIND_LABELS[task.kind] || { label: task.kind, color: 'gray' };
    return (
        <Box
            p={3} bg="var(--bg-input)" borderRadius="md"
            border="1px solid" borderColor="var(--border-secondary)"
            _hover={{ borderColor: 'var(--accent)', bg: 'var(--bg-card-hover)' }}
            transition="all 0.15s"
            cursor="pointer"
            onClick={onClick}
        >
            <HStack justify="space-between" mb={1}>
                <Text fontSize="sm" fontWeight="medium" color="var(--text-heading)" lineClamp={2}>
                    {task.title}
                </Text>
                <Badge
                    colorScheme={kind.color}
                    fontSize="2xs" variant="outline" flexShrink={0}
                >
                    {kind.label}
                </Badge>
            </HStack>
            {task.description && (
                <Text fontSize="xs" color="var(--text-tertiary)" lineClamp={2} mb={1}>
                    {task.description}
                </Text>
            )}
            <HStack gap={2} mt={1}>
                {task.gpu === 1 && (
                    <Badge colorScheme="red" fontSize="2xs" variant="outline">GPU</Badge>
                )}
                <Text fontSize="2xs" color="var(--text-muted)">P{task.priority}</Text>
                {task.retries > 0 && (
                    <Text fontSize="2xs" color="var(--text-secondary)">
                        retry {task.retries}/{task.max_retries}
                    </Text>
                )}
            </HStack>
        </Box>
    );
};

const KanbanColumn = ({ label, tasks, color, onTaskClick }: {
    label: string; tasks: Task[]; color: string; onTaskClick?: (t: Task) => void;
}) => (
    <Box flex={1} minW="180px" maxW="280px">
        <HStack mb={3} gap={2}>
            <Box w="8px" h="8px" borderRadius="full" bg={`${color}.400`} />
            <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">{label}</Text>
            <Badge colorScheme={color} fontSize="2xs" variant="subtle">{tasks.length}</Badge>
        </HStack>
        <VStack
            gap={2} align="stretch"
            bg="var(--bg-kanban-col)" borderRadius="lg" p={2}
            minH="120px" maxH="calc(100vh - 180px)" overflowY="auto"
            border="1px solid" borderColor="var(--border-primary)"
        >
            {tasks.length === 0 ? (
                <Text fontSize="xs" color="var(--text-muted)" textAlign="center" py={4}>Empty</Text>
            ) : (
                tasks.map(t => (
                    <KanbanCard key={t.id} task={t} onClick={() => onTaskClick?.(t)} />
                ))
            )}
        </VStack>
    </Box>
);

const TASK_STATUSES = ['pending', 'curating', 'ready', 'in_progress', 'review', 'completed', 'failed'] as const;

/* ─── Click-to-edit value display ────────────────────────────────── */
const clickableStyle = {
    cursor: 'pointer',
    borderRadius: 'md',
    px: 2,
    py: 1,
    mx: -2,
    _hover: { bg: 'var(--bg-input)' },
    transition: 'background 0.1s',
} as const;

/* ─── Task Detail Modal ──────────────────────────────────────────── */
interface TaskModalProps {
    task: Task;
    taskTree: Task[];
    agents: AgentDef[];
    project?: string;
    refinerAgent?: string;
    onUpdate: (id: string, patch: Partial<Task>) => Promise<void>;
    onClose: () => void;
}

type EditingField = null | 'title' | 'status' | 'priority' | 'kind' | 'gpu'
    | 'description' | 'agent' | 'assigned_to' | 'max_retries' | 'depends_on' | 'spec';

const TaskModal = ({ task, taskTree, agents, project, refinerAgent, onUpdate, onClose }: TaskModalProps) => {
    const [form, setForm] = useState({
        title: task.title,
        description: task.description || '',
        status: task.status,
        priority: task.priority,
        kind: task.kind,
        agent: task.agent || '',
        assigned_to: task.assigned_to || '',
        depends_on: task.depends_on || '',
        max_retries: task.max_retries,
        gpu: task.gpu,
        spec: task.spec || '',
    });
    const [saving, setSaving] = useState(false);
    const [dirty, setDirty] = useState(false);
    const [editing, setEditing] = useState<EditingField>(null);
    const [taskConvId, setTaskConvId] = useState<string | null>(null);

    useEffect(() => {
        setForm({
            title: task.title,
            description: task.description || '',
            status: task.status,
            priority: task.priority,
            kind: task.kind,
            agent: task.agent || '',
            assigned_to: task.assigned_to || '',
            depends_on: task.depends_on || '',
            max_retries: task.max_retries,
            gpu: task.gpu,
            spec: task.spec || '',
        });
        setDirty(false);
        setEditing(null);
    }, [task]);

    const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
        setForm(prev => ({ ...prev, [key]: value }));
        setDirty(true);
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const patch: Partial<Task> = {};
            if (form.title !== task.title) patch.title = form.title;
            if (form.description !== (task.description || '')) patch.description = form.description;
            if (form.status !== task.status) patch.status = form.status;
            if (form.priority !== task.priority) patch.priority = form.priority;
            if (form.kind !== task.kind) patch.kind = form.kind;
            if (form.agent !== (task.agent || '')) patch.agent = form.agent;
            if (form.assigned_to !== (task.assigned_to || '')) patch.assigned_to = form.assigned_to;
            if (form.depends_on !== (task.depends_on || '')) patch.depends_on = form.depends_on;
            if (form.max_retries !== task.max_retries) patch.max_retries = form.max_retries;
            if (form.gpu !== task.gpu) patch.gpu = form.gpu;
            if (form.spec !== (task.spec || '')) patch.spec = form.spec;

            if (Object.keys(patch).length > 0) {
                await onUpdate(task.id, patch);
            }
            setDirty(false);
            setEditing(null);
        } finally {
            setSaving(false);
        }
    };

    const commit = (_field: EditingField) => {
        setEditing(null);
        if (dirty) handleSave();
    };

    const closeModal = useCallback(() => {
        if (dirty) handleSave();
        onClose();
    }, [dirty, handleSave, onClose]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
        if (e.key === 'Escape') {
            if (editing) { setEditing(null); return; }
            closeModal();
        }
    }, [editing, closeModal]);

    const children = taskTree.filter(t => t.id !== task.id);
    const kindInfo = KIND_LABELS[form.kind] || { label: form.kind, color: 'gray' };

    const taskContext = [
        `Task: ${form.title}`,
        `Status: ${form.status}`,
        form.description && `Description: ${form.description}`,
        form.spec && `Spec: ${form.spec}`,
        form.agent && `Agent: ${form.agent}`,
        `Priority: P${form.priority}`,
    ].filter(Boolean).join('\n');

    return createPortal(
        <Box
            position="fixed" inset={0} zIndex={1400}
            display="flex" alignItems="center" justifyContent="center"
            onKeyDown={handleKeyDown}
        >
            <Box position="absolute" inset={0} bg="blackAlpha.600" onClick={closeModal} />
            <HStack
                position="relative" zIndex={1}
                bg="var(--bg-page)" borderRadius="xl"
                border="1px solid" borderColor="var(--border-primary)"
                boxShadow="xl"
                w="90vw" maxW="1200px" h="85vh"
                mx={4} gap={0} align="stretch"
                overflow="hidden"
            >
                {/* Left: Task fields */}
                <Box flex={1} display="flex" flexDirection="column" minW={0}>
                    {/* Header */}
                    <HStack px={5} py={4} borderBottom="1px solid" borderColor="var(--border-primary)" justify="space-between">
                        <HStack gap={2} flex={1} minW={0}>
                            <Heading size="sm" color="var(--text-heading)" lineClamp={1}>Task Detail</Heading>
                            <Text fontSize="2xs" color="var(--text-muted)" fontFamily="mono">{task.id}</Text>
                        </HStack>
                        <HStack gap={2}>
                            {dirty && (
                                <Button size="sm" colorScheme="green" onClick={handleSave} loading={saving} disabled={saving}>
                                    Save
                                </Button>
                            )}
                            <IconButton aria-label="Close" variant="ghost" size="sm" color="var(--text-tertiary)"
                                _hover={{ color: 'var(--text-heading)' }} onClick={closeModal}>
                                <CloseIcon />
                            </IconButton>
                        </HStack>
                    </HStack>

                    {/* Body */}
                    <Box flex={1} overflowY="auto" px={5} py={4}>
                        <VStack gap={4} align="stretch">
                            {/* Title */}
                            <Field label="Title">
                                {editing === 'title' ? (
                                    <Input
                                        size="sm" value={form.title}
                                        onChange={e => set('title', e.target.value)}
                                        onBlur={() => commit('title')}
                                        onKeyDown={e => { if (e.key === 'Enter') { e.stopPropagation(); commit('title'); } }}
                                        fontWeight="medium" autoFocus
                                        {...inputStyle}
                                    />
                                ) : (
                                    <Text
                                        fontSize="md" fontWeight="semibold" color="var(--text-heading)"
                                        onClick={() => setEditing('title')}
                                        {...clickableStyle}
                                    >
                                        {form.title || '—'}
                                    </Text>
                                )}
                            </Field>

                            {/* Status + Priority + Kind + GPU */}
                            <HStack gap={4} flexWrap="wrap">
                                <Field label="Status">
                                    {editing === 'status' ? (
                                        <NativeSelect.Root size="sm">
                                            <NativeSelect.Field value={form.status}
                                                onChange={e => { set('status', e.target.value); }}
                                                onBlur={() => commit('status')} autoFocus {...inputStyle}>
                                                {TASK_STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
                                            </NativeSelect.Field>
                                        </NativeSelect.Root>
                                    ) : (
                                        <Box onClick={() => setEditing('status')} {...clickableStyle}>
                                            <Badge colorScheme={STATUS_COLORS[form.status] || 'gray'}>{form.status}</Badge>
                                        </Box>
                                    )}
                                </Field>
                                <Field label="Priority">
                                    {editing === 'priority' ? (
                                        <Input size="sm" type="number" w="70px" value={form.priority}
                                            onChange={e => set('priority', parseInt(e.target.value) || 0)}
                                            onBlur={() => commit('priority')}
                                            onKeyDown={e => { if (e.key === 'Enter') { e.stopPropagation(); commit('priority'); } }}
                                            autoFocus {...inputStyle} />
                                    ) : (
                                        <Text fontSize="sm" color="var(--text-secondary)"
                                            onClick={() => setEditing('priority')} {...clickableStyle}>
                                            P{form.priority}
                                        </Text>
                                    )}
                                </Field>
                                <Field label="Kind">
                                    {editing === 'kind' ? (
                                        <NativeSelect.Root size="sm">
                                            <NativeSelect.Field value={form.kind}
                                                onChange={e => { set('kind', e.target.value); }}
                                                onBlur={() => commit('kind')} autoFocus {...inputStyle}>
                                                {['task', 'llm_complete', 'tool_call'].map(k => <option key={k} value={k}>{k}</option>)}
                                            </NativeSelect.Field>
                                        </NativeSelect.Root>
                                    ) : (
                                        <Box onClick={() => setEditing('kind')} {...clickableStyle}>
                                            <Badge colorScheme={kindInfo.color} variant="outline">{kindInfo.label}</Badge>
                                        </Box>
                                    )}
                                </Field>
                                <Field label="GPU">
                                    {editing === 'gpu' ? (
                                        <NativeSelect.Root size="sm" w="80px">
                                            <NativeSelect.Field value={String(form.gpu)}
                                                onChange={e => { set('gpu', parseInt(e.target.value) || 0); }}
                                                onBlur={() => commit('gpu')} autoFocus {...inputStyle}>
                                                <option value="0">No</option>
                                                <option value="1">Yes</option>
                                            </NativeSelect.Field>
                                        </NativeSelect.Root>
                                    ) : (
                                        <Box onClick={() => setEditing('gpu')} {...clickableStyle}>
                                            {form.gpu === 1
                                                ? <Badge colorScheme="red" fontSize="2xs" variant="outline">GPU</Badge>
                                                : <Text fontSize="sm" color="var(--text-muted)">No</Text>}
                                        </Box>
                                    )}
                                </Field>
                            </HStack>

                            {/* Description */}
                            <Field label="Description">
                                {editing === 'description' ? (
                                    <Textarea
                                        size="sm" rows={6} value={form.description}
                                        onChange={e => set('description', e.target.value)}
                                        onBlur={() => commit('description')}
                                        placeholder="Task description…" autoFocus
                                        {...inputStyle}
                                    />
                                ) : (
                                    <Box onClick={() => setEditing('description')} {...clickableStyle} minH="40px">
                                        {form.description ? (
                                            <Box fontSize="sm" color="var(--text-secondary)">
                                                <Markdown content={form.description} />
                                            </Box>
                                        ) : (
                                            <Text fontSize="sm" color="var(--text-muted)">Click to add description…</Text>
                                        )}
                                    </Box>
                                )}
                            </Field>

                            {/* Spec */}
                            <Field label="Spec">
                                {editing === 'spec' ? (
                                    <Textarea
                                        size="sm" rows={5} value={form.spec}
                                        onChange={e => set('spec', e.target.value)}
                                        onBlur={() => commit('spec')}
                                        placeholder="Task specification…"
                                        fontFamily="mono" fontSize="xs" autoFocus
                                        {...inputStyle}
                                    />
                                ) : (
                                    <Box onClick={() => setEditing('spec')} {...clickableStyle} minH="32px">
                                        {form.spec ? (
                                            <Text fontSize="xs" fontFamily="mono" color="var(--text-secondary)" whiteSpace="pre-wrap">
                                                {form.spec}
                                            </Text>
                                        ) : (
                                            <Text fontSize="xs" color="var(--text-muted)">Click to add spec…</Text>
                                        )}
                                    </Box>
                                )}
                            </Field>

                            {/* Agent + Assigned to + Max retries + Depends on */}
                            <HStack gap={4} flexWrap="wrap">
                                <Field label="Agent">
                                    {editing === 'agent' ? (
                                        <NativeSelect.Root size="sm">
                                            <NativeSelect.Field value={form.agent}
                                                onChange={e => { set('agent', e.target.value); }}
                                                onBlur={() => commit('agent')} autoFocus {...inputStyle}>
                                                <option value="">— none —</option>
                                                {agents.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}
                                            </NativeSelect.Field>
                                        </NativeSelect.Root>
                                    ) : (
                                        <Text fontSize="sm"
                                            color={form.agent ? 'var(--text-secondary)' : 'var(--text-muted)'}
                                            onClick={() => setEditing('agent')} {...clickableStyle}>
                                            {form.agent || '— none —'}
                                        </Text>
                                    )}
                                </Field>
                                <Field label="Assigned to">
                                    {editing === 'assigned_to' ? (
                                        <Input size="sm" value={form.assigned_to}
                                            onChange={e => set('assigned_to', e.target.value)}
                                            onBlur={() => commit('assigned_to')}
                                            onKeyDown={e => { if (e.key === 'Enter') { e.stopPropagation(); commit('assigned_to'); } }}
                                            placeholder="worker id" autoFocus {...inputStyle} />
                                    ) : (
                                        <Text fontSize="sm"
                                            color={form.assigned_to ? 'var(--text-secondary)' : 'var(--text-muted)'}
                                            onClick={() => setEditing('assigned_to')} {...clickableStyle}>
                                            {form.assigned_to || '—'}
                                        </Text>
                                    )}
                                </Field>
                                <Field label="Max retries">
                                    {editing === 'max_retries' ? (
                                        <Input size="sm" type="number" w="70px" value={form.max_retries}
                                            onChange={e => set('max_retries', parseInt(e.target.value) || 0)}
                                            onBlur={() => commit('max_retries')}
                                            onKeyDown={e => { if (e.key === 'Enter') { e.stopPropagation(); commit('max_retries'); } }}
                                            autoFocus {...inputStyle} />
                                    ) : (
                                        <Text fontSize="sm" color="var(--text-secondary)"
                                            onClick={() => setEditing('max_retries')} {...clickableStyle}>
                                            {form.max_retries}
                                        </Text>
                                    )}
                                </Field>
                                <Field label="Depends on">
                                    {editing === 'depends_on' ? (
                                        <Input size="sm" value={form.depends_on}
                                            onChange={e => set('depends_on', e.target.value)}
                                            onBlur={() => commit('depends_on')}
                                            onKeyDown={e => { if (e.key === 'Enter') { e.stopPropagation(); commit('depends_on'); } }}
                                            placeholder="task id" fontFamily="mono" fontSize="xs" autoFocus {...inputStyle} />
                                    ) : (
                                        <Text fontSize="xs" fontFamily="mono"
                                            color={form.depends_on ? 'var(--text-secondary)' : 'var(--text-muted)'}
                                            onClick={() => setEditing('depends_on')} {...clickableStyle}>
                                            {form.depends_on || '—'}
                                        </Text>
                                    )}
                                </Field>
                            </HStack>

                            {/* Metadata */}
                            <Box borderTop="1px solid" borderColor="var(--border-primary)" pt={3} mt={1}>
                                <Text fontSize="2xs" fontWeight="medium" color="var(--text-muted)" mb={2}>Metadata</Text>
                                <HStack gap={6} flexWrap="wrap">
                                    <Box>
                                        <Text fontSize="2xs" color="var(--text-muted)">Created</Text>
                                        <Text fontSize="2xs" color="var(--text-secondary)">{task.created_at}</Text>
                                    </Box>
                                    {task.updated_at && (
                                        <Box>
                                            <Text fontSize="2xs" color="var(--text-muted)">Updated</Text>
                                            <Text fontSize="2xs" color="var(--text-secondary)">{task.updated_at}</Text>
                                        </Box>
                                    )}
                                    {task.started_at && (
                                        <Box>
                                            <Text fontSize="2xs" color="var(--text-muted)">Started</Text>
                                            <Text fontSize="2xs" color="var(--text-secondary)">{task.started_at}</Text>
                                        </Box>
                                    )}
                                    <Box>
                                        <Text fontSize="2xs" color="var(--text-muted)">Retries</Text>
                                        <Text fontSize="2xs" color="var(--text-secondary)">{task.retries}/{task.max_retries}</Text>
                                    </Box>
                                </HStack>
                            </Box>

                            {/* Error log */}
                            {task.error_log && (
                                <Box bg="var(--bg-error)" p={3} borderRadius="md">
                                    <Text fontSize="2xs" fontWeight="medium" color="var(--text-error)" mb={1}>Error Log</Text>
                                    <Text fontSize="xs" color="var(--text-error)" fontFamily="mono" whiteSpace="pre-wrap">
                                        {task.error_log}
                                    </Text>
                                </Box>
                            )}

                            {/* Subtasks */}
                            {children.length > 0 && (
                                <Box borderTop="1px solid" borderColor="var(--border-primary)" pt={3}>
                                    <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)" mb={2}>
                                        Subtasks ({children.length})
                                    </Text>
                                    <VStack align="stretch" gap={1}>
                                        {children.map(child => (
                                            <HStack
                                                key={child.id} p={2} bg="var(--bg-card)" borderRadius="md"
                                                border="1px solid" borderColor="var(--border-primary)" gap={2}
                                            >
                                                <Badge colorScheme={STATUS_COLORS[child.status] || 'gray'} fontSize="2xs">
                                                    {child.status}
                                                </Badge>
                                                <Text fontSize="xs" color="var(--text-secondary)" flex={1} lineClamp={1}>
                                                    {child.title}
                                                </Text>
                                                <Badge
                                                    colorScheme={(KIND_LABELS[child.kind] || { color: 'gray' }).color}
                                                    fontSize="2xs" variant="outline">
                                                    {(KIND_LABELS[child.kind] || { label: child.kind }).label}
                                                </Badge>
                                            </HStack>
                                        ))}
                                    </VStack>
                                </Box>
                            )}
                        </VStack>
                    </Box>
                </Box>

                {/* Right: Chat for task refinement */}
                <Box
                    w="420px" flexShrink={0}
                    borderLeft="1px solid" borderColor="var(--border-primary)"
                    display="flex" flexDirection="column"
                    bg="var(--bg-page)"
                >
                    <Box px={4} py={4} borderBottom="1px solid" borderColor="var(--border-primary)">
                        <Text fontSize="sm" fontWeight="semibold" color="var(--text-heading)">Refine Task</Text>
                        <Text fontSize="2xs" color="var(--text-muted)" mt={1}>
                            Chat with AI to refine this task. The current task info is shared as context.
                        </Text>
                    </Box>
                    <ChatPanel
                        conversationId={taskConvId}
                        onConversationCreated={setTaskConvId}
                        project={project}
                        refinerAgent={refinerAgent}
                        compact
                        placeholder={`Refine task "${form.title}"… (task context is included)`}
                        autoSendMessage={`I'd like to work on refining the following task. Here is its current state:\n\n${taskContext}\n\nPlease help me improve and clarify this task.`}
                    />
                </Box>
            </HStack>
        </Box>,
        document.body,
    );
};

const ProjectView = () => {
    const { name } = useParams<{ name: string }>();
    const navigate = useNavigate();
    const { tasks: wsTasks, isConnected } = useAgentSocket();

    const [project, setProject] = useState<Project | null>(null);
    const [tasks, setTasks] = useState<Task[]>([]);
    const [newTaskTitle, setNewTaskTitle] = useState('');
    const [showNewTask, setShowNewTask] = useState(false);
    const [selectedTask, setSelectedTask] = useState<Task | null>(null);
    const [taskTree, setTaskTree] = useState<Task[]>([]);
    const [convId, setConvId] = useState<string | null>(null);
    const [editModalOpen, setEditModalOpen] = useState(false);
    const [agentChoices, setAgentChoices] = useState<AgentDef[]>([]);

    useEffect(() => {
        if (!name) return;
        document.title = `${name} - Açaí`;
        getProject(name).then(setProject).catch(() => navigate('/projects'));
    }, [name, navigate]);

    useEffect(() => {
        listAgents().then(setAgentChoices).catch(() => {});
    }, []);

    useEffect(() => {
        if (wsTasks.length > 0) {
            setTasks(wsTasks.filter(t => t.project === name && !t.parent_task));
        } else if (!isConnected && name) {
            listTasks({ project: name, rootOnly: true }).then(setTasks).catch(() => {});
        }
    }, [wsTasks, isConnected, name]);

    const handleCreateTask = async () => {
        const title = newTaskTitle.trim();
        if (!title || !name) return;
        try {
            const task = await createTask(title, '', 0, name);
            setTasks(prev => [task, ...prev]);
            setNewTaskTitle('');
            setShowNewTask(false);
        } catch { /* ignore */ }
    };

    const handleTaskClick = useCallback(async (task: Task) => {
        setSelectedTask(task);
        try {
            const tree = await getTaskTree(task.id);
            setTaskTree(tree);
        } catch {
            setTaskTree([task]);
        }
    }, []);

    const handleUpdateTask = useCallback(async (id: string, patch: Partial<Task>) => {
        const updated = await updateTask(id, patch);
        setTasks(prev => prev.map(t => t.id === id ? updated : t));
        setSelectedTask(prev => prev?.id === id ? updated : prev);
    }, []);

    const handleSaveProject = async (patch: Partial<Project>) => {
        if (!name) return;
        await updateProject(name, patch);
        const p = await getProject(name);
        setProject(p);
        setEditModalOpen(false);
    };

    if (!project) {
        return (
            <Box h="100vh" w="100%" bg="var(--bg-page)" display="flex" alignItems="center" justifyContent="center">
                <Spinner color="var(--accent)" size="lg" />
            </Box>
        );
    }

    return (
        <Box h="100vh" w="100%" bg="var(--bg-page)" display="flex" flexDirection="column" overflow="hidden">
            {/* Header */}
            <HStack
                px={4} py={3}
                borderBottom="1px solid" borderColor="var(--border-primary)"
                bg="var(--bg-page)" flexShrink={0}
            >
                <IconButton
                    aria-label="Back to projects"
                    onClick={() => navigate('/projects')}
                    variant="ghost" size="sm" color="var(--text-tertiary)"
                    _hover={{ color: 'var(--text-heading)' }}
                >
                    <BackIcon />
                </IconButton>
                <HStack gap={2} flex={1}>
                    <Heading size="md" color="var(--text-heading)">{project.name}</Heading>
                    <Badge colorScheme="blue" fontSize="xs" variant="outline">{project.language}</Badge>
                    <Badge colorScheme={project.source === 'clone' ? 'purple' : 'green'} fontSize="xs">
                        {project.source}
                    </Badge>
                </HStack>
                <IconButton
                    aria-label="Edit project"
                    onClick={() => setEditModalOpen(true)}
                    variant="ghost" size="sm" color="var(--text-tertiary)"
                    _hover={{ color: 'var(--text-heading)' }}
                >
                    <PencilIcon />
                </IconButton>
                <Text fontSize="xs" color="var(--text-muted)" fontFamily="mono" maxW="40%" lineClamp={1}>
                    {project.path}
                </Text>
            </HStack>

            {/* Edit modal (portal) */}
            {editModalOpen && (
                <ProjectEditModal
                    project={project}
                    agents={agentChoices}
                    onSave={handleSaveProject}
                    onClose={() => setEditModalOpen(false)}
                />
            )}

            {/* Task Modal (portal) */}
            {selectedTask && (
                <TaskModal
                    task={selectedTask}
                    taskTree={taskTree}
                    agents={agentChoices}
                    project={name}
                    refinerAgent={project.refiner || 'refiner'}
                    onUpdate={handleUpdateTask}
                    onClose={() => { setSelectedTask(null); setTaskTree([]); }}
                />
            )}

            {/* Main: Kanban + Chat */}
            <HStack flex={1} minH={0} align="stretch" gap={0}>
                {/* Kanban Board */}
                <Box flex={1} overflowX="auto" overflowY="hidden" display="flex" flexDirection="column">
                    <HStack px={4} pt={3} pb={1} gap={2}>
                        {!showNewTask ? (
                            <Button size="xs" colorScheme="green" variant="outline" onClick={() => setShowNewTask(true)}>
                                + New Task
                            </Button>
                        ) : (
                            <>
                                <Input
                                    size="xs" placeholder="Task title" bg="var(--bg-card)"
                                    color="var(--text-heading)" borderColor="var(--border-secondary)" flex={1} maxW="300px"
                                    value={newTaskTitle} onChange={e => setNewTaskTitle(e.target.value)}
                                    onKeyDown={e => e.key === 'Enter' && handleCreateTask()}
                                    autoFocus
                                />
                                <Button size="xs" colorScheme="green" onClick={handleCreateTask} disabled={!newTaskTitle.trim()}>
                                    Add
                                </Button>
                                <Button size="xs" variant="ghost" color="var(--text-tertiary)" onClick={() => { setShowNewTask(false); setNewTaskTitle(''); }}>
                                    Cancel
                                </Button>
                            </>
                        )}
                    </HStack>
                    <Box flex={1} overflowX="auto" overflowY="hidden" p={4} pt={2}>
                        <HStack gap={3} align="flex-start" h="100%" minW="min-content">
                            {COLUMNS.map(col => (
                                <KanbanColumn
                                    key={col.key}
                                    label={col.label}
                                    color={STATUS_COLORS[col.key] || 'gray'}
                                    tasks={tasks.filter(t => t.status === col.key)}
                                    onTaskClick={handleTaskClick}
                                />
                            ))}
                        </HStack>
                    </Box>
                </Box>

                {/* Chat Panel */}
                <Box
                    w="520px" flexShrink={0}
                    borderLeft="1px solid" borderColor="var(--border-primary)"
                    display="flex" flexDirection="column"
                    bg="var(--bg-page)"
                >
                    <Box px={4} py={3} borderBottom="1px solid" borderColor="var(--border-primary)">
                        <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">Chat</Text>
                    </Box>
                    <ChatPanel
                        conversationId={convId}
                        onConversationCreated={setConvId}
                        project={name}
                        refinerAgent={project.refiner || 'refiner'}
                        compact
                    />
                </Box>
            </HStack>
        </Box>
    );
};

export default ProjectView;
