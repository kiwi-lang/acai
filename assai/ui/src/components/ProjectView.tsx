import { useState, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Box, VStack, HStack, Text, Heading, Badge, IconButton, Spinner,
    Input, Button, NativeSelect,
} from '@chakra-ui/react';
import { getProject, listTasks, createTask, getTaskTree, listAgents, updateProject } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { Project, Task, AgentDef } from '../services/types';
import ChatPanel from './ChatPanel';

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
        document.title = `${name} - ASSAI`;
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

                {/* Task Detail Panel */}
                {selectedTask && (
                    <Box
                        w="360px" flexShrink={0}
                        borderLeft="1px solid" borderColor="var(--border-primary)"
                        display="flex" flexDirection="column"
                        bg="var(--bg-page)" overflowY="auto"
                    >
                        <HStack px={4} py={3} borderBottom="1px solid" borderColor="var(--border-primary)" justify="space-between">
                            <Text fontSize="sm" fontWeight="semibold" color="var(--text-heading)" lineClamp={1}>
                                {selectedTask.title}
                            </Text>
                            <IconButton
                                aria-label="Close" variant="ghost" size="xs" color="var(--text-tertiary)"
                                _hover={{ color: 'var(--text-heading)' }}
                                onClick={() => { setSelectedTask(null); setTaskTree([]); }}
                            >
                                <Text fontSize="lg">&times;</Text>
                            </IconButton>
                        </HStack>
                        <Box px={4} py={3}>
                            <VStack align="stretch" gap={3}>
                                <HStack gap={2}>
                                    <Badge colorScheme={STATUS_COLORS[selectedTask.status] || 'gray'}>
                                        {selectedTask.status}
                                    </Badge>
                                    <Badge colorScheme={(KIND_LABELS[selectedTask.kind] || { color: 'gray' }).color} variant="outline">
                                        {(KIND_LABELS[selectedTask.kind] || { label: selectedTask.kind }).label}
                                    </Badge>
                                </HStack>
                                {selectedTask.description && (
                                    <Text fontSize="sm" color="var(--text-secondary)">{selectedTask.description}</Text>
                                )}
                                <Text fontSize="xs" color="var(--text-muted)">
                                    Created: {selectedTask.created_at}
                                </Text>
                                {selectedTask.error_log && (
                                    <Box bg="var(--bg-error)" p={2} borderRadius="md">
                                        <Text fontSize="xs" color="var(--text-error)" fontFamily="mono" whiteSpace="pre-wrap">
                                            {selectedTask.error_log}
                                        </Text>
                                    </Box>
                                )}

                                {taskTree.length > 1 && (
                                    <>
                                        <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)" mt={2}>
                                            Subtasks ({taskTree.length - 1})
                                        </Text>
                                        <VStack align="stretch" gap={1}>
                                            {taskTree.filter(t => t.id !== selectedTask.id).map(child => (
                                                <HStack
                                                    key={child.id} p={2} bg="var(--bg-card)" borderRadius="md"
                                                    border="1px solid" borderColor="var(--border-primary)" gap={2}
                                                >
                                                    <Badge
                                                        colorScheme={STATUS_COLORS[child.status] || 'gray'}
                                                        fontSize="2xs"
                                                    >
                                                        {child.status}
                                                    </Badge>
                                                    <Text fontSize="xs" color="var(--text-secondary)" flex={1} lineClamp={1}>
                                                        {child.title}
                                                    </Text>
                                                    <Badge
                                                        colorScheme={(KIND_LABELS[child.kind] || { color: 'gray' }).color}
                                                        fontSize="2xs" variant="outline"
                                                    >
                                                        {(KIND_LABELS[child.kind] || { label: child.kind }).label}
                                                    </Badge>
                                                </HStack>
                                            ))}
                                        </VStack>
                                    </>
                                )}
                            </VStack>
                        </Box>
                    </Box>
                )}

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
