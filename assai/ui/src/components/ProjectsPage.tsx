import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    Box, VStack, HStack, Text, Button, Heading, Badge,
    Input, Textarea, NativeSelect,
} from '@chakra-ui/react';
import { listProjects, createProject } from '../services/api';
import type { Project } from '../services/types';

const LANGUAGES = ['python', 'rust', 'javascript', 'typescript', 'go', 'c', 'cpp', 'java', 'other'];
const PROVIDERS = ['', 'github', 'gitlab', 'bitbucket'];
const TEMPLATES = ['default', 'library', 'cli', 'web-api', 'minimal'];

const LANG_COLORS: Record<string, string> = {
    python: 'blue', rust: 'orange', javascript: 'yellow', typescript: 'cyan',
    go: 'teal', c: 'gray', cpp: 'gray', java: 'red', other: 'gray',
};

const ProjectCard = ({ project, onClick }: { project: Project; onClick: () => void }) => (
    <Box
        p={4} bg="var(--bg-card)" borderRadius="lg"
        border="1px solid" borderColor="var(--border-primary)"
        _hover={{ borderColor: 'var(--accent)', cursor: 'pointer' }}
        transition="all 0.2s"
        onClick={onClick}
    >
        <HStack justify="space-between" mb={2}>
            <HStack gap={2}>
                <Badge colorScheme={LANG_COLORS[project.language] || 'gray'} fontSize="xs" variant="outline">
                    {project.language}
                </Badge>
                <Text fontWeight="semibold" color="var(--text-heading)" fontSize="md">
                    {project.name}
                </Text>
            </HStack>
            <Badge colorScheme={project.source === 'clone' ? 'purple' : 'green'} fontSize="xs">
                {project.source}
            </Badge>
        </HStack>

        <Text fontSize="sm" color="var(--text-tertiary)" fontFamily="mono" mb={2}>
            {project.path}
        </Text>

        <HStack gap={4} flexWrap="wrap">
            {project.language === 'python' && (
                <>
                    <Text fontSize="xs" color="var(--text-muted)">
                        Python {project.python_version}
                    </Text>
                    <Text fontSize="xs" color="var(--text-muted)">
                        venv: {project.venv_path}
                    </Text>
                </>
            )}
            {project.repo_url && (
                <Text fontSize="xs" color="var(--text-muted)" overflow="hidden" textOverflow="ellipsis">
                    {project.repo_url}
                </Text>
            )}
            <Text fontSize="xs" color="var(--text-muted)">
                {new Date(project.created_at).toLocaleString()}
            </Text>
        </HStack>
    </Box>
);

const ProjectsPage = () => {
    const navigate = useNavigate();
    const [projects, setProjects] = useState<Project[]>([]);
    const [showCreate, setShowCreate] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Form state
    const [name, setName] = useState('');
    const [language, setLanguage] = useState('python');
    const [source, setSource] = useState<'new' | 'clone'>('new');
    const [template, setTemplate] = useState('default');
    const [repoUrl, setRepoUrl] = useState('');
    const [provider, setProvider] = useState('');
    const [pythonVersion, setPythonVersion] = useState('3.12');
    const [venvPath, setVenvPath] = useState('.venv');

    const loadProjects = async () => {
        try {
            const data = await listProjects();
            setProjects(data);
        } catch {
            // silently handle
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        document.title = 'Projects - ASSAI';
        loadProjects();
    }, []);

    const resetForm = () => {
        setName('');
        setLanguage('python');
        setSource('new');
        setTemplate('default');
        setRepoUrl('');
        setProvider('');
        setPythonVersion('3.12');
        setVenvPath('.venv');
        setError('');
    };

    const handleCreate = async () => {
        if (!name.trim()) return;
        if (source === 'clone' && !repoUrl.trim()) {
            setError('Repository URL is required for clone');
            return;
        }

        setError('');
        try {
            await createProject({
                name: name.trim(),
                language,
                source,
                template,
                repo_url: repoUrl.trim(),
                provider,
                python_version: pythonVersion,
                venv_path: venvPath,
            });
            resetForm();
            setShowCreate(false);
            loadProjects();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to create project');
        }
    };

    return (
        <Box h="100vh" w="100%" bg="var(--bg-page)" overflowY="auto" p={6}>
            <Box maxW="4xl" mx="auto">
                <HStack justify="space-between" mb={6}>
                    <Heading size="lg" color="var(--text-heading)">Projects</Heading>
                    <Button
                        colorScheme="green" size="sm"
                        onClick={() => { setShowCreate(!showCreate); if (showCreate) resetForm(); }}
                    >
                        {showCreate ? 'Cancel' : 'New Project'}
                    </Button>
                </HStack>

                {/* Create form */}
                {showCreate && (
                    <Box p={5} bg="var(--bg-card)" borderRadius="lg" mb={6} border="1px solid" borderColor="var(--border-primary)">
                        <VStack gap={4} align="stretch">
                            <Heading size="md" color="var(--text-heading)">Create Project</Heading>

                            {error && (
                                <Box p={3} bg="var(--bg-error)" borderRadius="md">
                                    <Text color="var(--text-error)" fontSize="sm">{error}</Text>
                                </Box>
                            )}

                            {/* Name */}
                            <Box>
                                <Text fontSize="sm" color="var(--text-tertiary)" mb={1}>Project Name</Text>
                                <Input
                                    placeholder="my-project"
                                    value={name}
                                    onChange={e => setName(e.target.value)}
                                    bg="var(--bg-input)" border="none" color="var(--text-heading)"
                                    _placeholder={{ color: 'var(--text-muted)' }}
                                />
                            </Box>

                            {/* Language */}
                            <Box>
                                <Text fontSize="sm" color="var(--text-tertiary)" mb={1}>Language</Text>
                                <NativeSelect.Root>
                                    <NativeSelect.Field
                                        value={language}
                                        onChange={e => setLanguage(e.target.value)}
                                        bg="var(--bg-input)" border="none" color="var(--text-heading)"
                                    >
                                        {LANGUAGES.map(l => (
                                            <option key={l} value={l} style={{ background: 'var(--option-bg)' }}>{l}</option>
                                        ))}
                                    </NativeSelect.Field>
                                </NativeSelect.Root>
                            </Box>

                            {/* Source: new or clone */}
                            <Box>
                                <Text fontSize="sm" color="var(--text-tertiary)" mb={2}>Source</Text>
                                <HStack gap={2}>
                                    <Button
                                        size="sm" flex={1}
                                        variant={source === 'new' ? 'solid' : 'outline'}
                                        colorScheme={source === 'new' ? 'green' : 'gray'}
                                        onClick={() => setSource('new')}
                                    >
                                        New Project
                                    </Button>
                                    <Button
                                        size="sm" flex={1}
                                        variant={source === 'clone' ? 'solid' : 'outline'}
                                        colorScheme={source === 'clone' ? 'purple' : 'gray'}
                                        onClick={() => setSource('clone')}
                                    >
                                        Clone Repository
                                    </Button>
                                </HStack>
                            </Box>

                            {/* New project: template */}
                            {source === 'new' && (
                                <Box>
                                    <Text fontSize="sm" color="var(--text-tertiary)" mb={1}>Template</Text>
                                    <NativeSelect.Root>
                                        <NativeSelect.Field
                                            value={template}
                                            onChange={e => setTemplate(e.target.value)}
                                            bg="var(--bg-input)" border="none" color="var(--text-heading)"
                                        >
                                            {TEMPLATES.map(t => (
                                                <option key={t} value={t} style={{ background: 'var(--option-bg)' }}>{t}</option>
                                            ))}
                                        </NativeSelect.Field>
                                    </NativeSelect.Root>
                                </Box>
                            )}

                            {/* Clone: repo URL + provider */}
                            {source === 'clone' && (
                                <>
                                    <Box>
                                        <Text fontSize="sm" color="var(--text-tertiary)" mb={1}>Repository URL</Text>
                                        <Input
                                            placeholder="https://github.com/user/repo.git"
                                            value={repoUrl}
                                            onChange={e => setRepoUrl(e.target.value)}
                                            bg="var(--bg-input)" border="none" color="var(--text-heading)"
                                            _placeholder={{ color: 'var(--text-muted)' }}
                                        />
                                    </Box>
                                    <Box>
                                        <Text fontSize="sm" color="var(--text-tertiary)" mb={1}>Provider (optional)</Text>
                                        <NativeSelect.Root>
                                            <NativeSelect.Field
                                                value={provider}
                                                onChange={e => setProvider(e.target.value)}
                                                bg="var(--bg-input)" border="none" color="var(--text-heading)"
                                            >
                                                <option value="" style={{ background: 'var(--option-bg)' }}>Auto-detect</option>
                                                {PROVIDERS.filter(Boolean).map(p => (
                                                    <option key={p} value={p} style={{ background: 'var(--option-bg)' }}>{p}</option>
                                                ))}
                                            </NativeSelect.Field>
                                        </NativeSelect.Root>
                                    </Box>
                                </>
                            )}

                            {/* Python-specific settings */}
                            {language === 'python' && (
                                <Box p={4} bg="var(--bg-elevated)" borderRadius="md" border="1px solid" borderColor="var(--border-secondary)">
                                    <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)" mb={3}>Python Settings</Text>
                                    <HStack gap={4}>
                                        <Box flex={1}>
                                            <Text fontSize="xs" color="var(--text-tertiary)" mb={1}>Python Version</Text>
                                            <NativeSelect.Root>
                                                <NativeSelect.Field
                                                    value={pythonVersion}
                                                    onChange={e => setPythonVersion(e.target.value)}
                                                    bg="var(--bg-input)" border="none" color="var(--text-heading)"
                                                >
                                                    {['3.10', '3.11', '3.12', '3.13'].map(v => (
                                                        <option key={v} value={v} style={{ background: 'var(--option-bg)' }}>{v}</option>
                                                    ))}
                                                </NativeSelect.Field>
                                            </NativeSelect.Root>
                                        </Box>
                                        <Box flex={1}>
                                            <Text fontSize="xs" color="var(--text-tertiary)" mb={1}>Venv Path</Text>
                                            <Input
                                                value={venvPath}
                                                onChange={e => setVenvPath(e.target.value)}
                                                bg="var(--bg-input)" border="none" color="var(--text-heading)"
                                                size="sm"
                                            />
                                        </Box>
                                    </HStack>
                                </Box>
                            )}

                            {/* Scaffold preview */}
                            <Box p={4} bg="var(--bg-page)" borderRadius="md" border="1px solid" borderColor="var(--border-secondary)">
                                <Text fontSize="xs" fontWeight="semibold" color="var(--text-tertiary)" mb={2}>
                                    Will create:
                                </Text>
                                <Text fontSize="xs" color="var(--text-muted)" fontFamily="mono" whiteSpace="pre" lineHeight="1.8">
{`${name || '<project>'}/
├── docs/
│   ├── goal.md
│   ├── overview.md
│   ├── components/
│   └── recipes/
├── tests/
│   └── test_${(name || '<project>').replace(/-/g, '_').toLowerCase()}.py${language === 'python' ? `
├── ${(name || '<project>').replace(/-/g, '_').toLowerCase()}/
│   └── __init__.py
├── pyproject.toml` : ''}
└── README.md`}
                                </Text>
                            </Box>

                            <Button
                                colorScheme="green" size="md" alignSelf="flex-end"
                                onClick={handleCreate}
                                disabled={!name.trim() || (source === 'clone' && !repoUrl.trim())}
                            >
                                {source === 'clone' ? 'Clone & Scaffold' : 'Create Project'}
                            </Button>
                        </VStack>
                    </Box>
                )}

                {/* Project list */}
                <VStack gap={3} align="stretch">
                    {loading ? (
                        <Text color="var(--text-tertiary)" textAlign="center" py={8}>Loading...</Text>
                    ) : projects.length === 0 ? (
                        <VStack py={12} gap={3}>
                            <Text fontSize="lg" color="var(--text-tertiary)">No projects yet</Text>
                            <Text fontSize="sm" color="var(--text-muted)">
                                Create a new project or clone an existing repository to get started.
                            </Text>
                        </VStack>
                    ) : (
                        projects.map(project => (
                            <ProjectCard
                                key={project.id}
                                project={project}
                                onClick={() => navigate(`/projects/${project.name}`)}
                            />
                        ))
                    )}
                </VStack>
            </Box>
        </Box>
    );
};

export default ProjectsPage;
