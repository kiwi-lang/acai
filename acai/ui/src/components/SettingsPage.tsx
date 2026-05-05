import { useState, useEffect, useCallback, useRef } from 'react';
import {
    Box, VStack, HStack, Text, Heading, Input,
    NativeSelect, Spinner,
} from '@chakra-ui/react';
import {
    getConfig, updateConfig,
    getVersion, triggerUpdate,
    getGitBackupStatus, generateGitKey, setupGitBackup,
    triggerGitSync, testGitConnection,
    ensureTTSVoices,
    getTTSVoices,
    downloadTTSVoice,
} from '../services/api';
import type { VersionInfo, GitBackupStatus, TTSVoiceEntry, TTSDownloadProgress } from '../services/api';
import { toaster } from './ui/toaster';
import type { SystemConfig, SandboxConfig, CIConfig, TTSConfig } from '../services/types';

const SANDBOX_TYPES = ['none', 'docker', 'podman', 'firecracker', 'bubblewrap', 'nsjail'];

const SaveButton = ({ busy, onClick }: { busy: boolean; onClick: () => void }) => (
    <Box
        as="button"
        px={4} py={1.5}
        borderRadius="md"
        fontSize="sm"
        fontWeight="medium"
        bg="var(--accent)"
        color="var(--text-inverse)"
        cursor="pointer"
        onClick={onClick}
        _hover={{ bg: 'var(--accent-hover)' }}
    >
        {busy ? <Spinner size="xs" /> : 'Save'}
    </Box>
);

const ToggleButton = ({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) => (
    <Box
        as="button"
        px={3} py={1}
        borderRadius="md"
        fontSize="xs"
        fontWeight="medium"
        border="1px solid"
        borderColor={value ? 'var(--accent)' : 'var(--border-primary)'}
        bg={value ? 'var(--accent-subtle)' : 'transparent'}
        color={value ? 'var(--accent)' : 'var(--text-tertiary)'}
        cursor="pointer"
        onClick={() => onChange(!value)}
        _hover={{ borderColor: 'var(--accent)' }}
    >
        {label}: {value ? 'ON' : 'OFF'}
    </Box>
);

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <Box>
        <Text fontSize="xs" color="var(--text-muted)" mb={1}>{label}</Text>
        {children}
    </Box>
);

const SectionCard = ({
    title,
    children,
    busy,
    onSave,
    status,
}: {
    title: string;
    children: React.ReactNode;
    busy: boolean;
    onSave: () => void;
    status: string;
}) => (
    <Box
        p={4}
        bg="var(--bg-card)"
        borderRadius="lg"
        border="1px solid"
        borderColor="var(--border-primary)"
    >
        <HStack justify="space-between" mb={3}>
            <Text fontWeight="semibold" color="var(--text-heading)" fontSize="lg">{title}</Text>
            <HStack gap={2}>
                {status && (
                    <Text fontSize="xs" color={status === 'Saved' ? 'green.400' : 'var(--text-error)'}>{status}</Text>
                )}
                <SaveButton busy={busy} onClick={onSave} />
            </HStack>
        </HStack>
        <VStack gap={3} align="stretch">
            {children}
        </VStack>
    </Box>
);

const inputProps = {
    size: 'sm' as const,
    bg: 'var(--bg-input)',
    color: 'var(--text-primary)',
    borderColor: 'var(--border-input)',
};

const SettingsPage = () => {
    const [config, setConfig] = useState<SystemConfig | null>(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [sectionStatus, setSectionStatus] = useState<Record<string, string>>({});
    const [savingSection, setSavingSection] = useState('');
    const [ttsVoices, setTtsVoices] = useState<TTSVoiceEntry[]>([]);
    const [ttsDownloading, setTtsDownloading] = useState(false);

    const refresh = useCallback(() => {
        setLoading(true);
        getConfig()
            .then(c => { setConfig(c); setError(''); })
            .catch(err => setError(err instanceof Error ? err.message : 'Failed to load config'))
            .finally(() => setLoading(false));
        ensureTTSVoices().then(setTtsVoices).catch(() => {});
    }, []);

    useEffect(() => {
        document.title = 'Settings - Açaí';
        refresh();
    }, [refresh]);

    const saveSection = async (section: string) => {
        if (!config) return;
        setSavingSection(section);
        setSectionStatus(prev => ({ ...prev, [section]: '' }));
        try {
            const patch = { [section]: (config as any)[section] };
            const updated = await updateConfig(patch);
            setConfig(updated);
            setSectionStatus(prev => ({ ...prev, [section]: 'Saved' }));
            setTimeout(() => setSectionStatus(prev => ({ ...prev, [section]: '' })), 2000);
        } catch (err) {
            setSectionStatus(prev => ({ ...prev, [section]: err instanceof Error ? err.message : 'Error' }));
        } finally {
            setSavingSection('');
        }
    };

    const updateSandbox = (key: keyof SandboxConfig, value: any) => {
        if (!config) return;
        setConfig({ ...config, sandbox: { ...config.sandbox, [key]: value } });
    };

    const updateWorker = (key: string, value: any) => {
        if (!config) return;
        setConfig({ ...config, worker: { ...config.worker, [key]: value } });
    };

    const updateGit = (key: string, value: any) => {
        if (!config) return;
        setConfig({ ...config, git: { ...config.git, [key]: value } });
    };

    const updateQueue = (key: string, value: any) => {
        if (!config) return;
        setConfig({ ...config, queue: { ...config.queue, [key]: value } });
    };

    const updateAudit = (key: string, value: any) => {
        if (!config) return;
        setConfig({ ...config, audit: { ...config.audit, [key]: value } });
    };

    const updateCI = (key: keyof CIConfig, value: any) => {
        if (!config) return;
        setConfig({ ...config, ci: { ...config.ci, [key]: value } });
    };

    const updateTTS = (key: keyof TTSConfig, value: any) => {
        if (!config) return;
        setConfig({ ...config, tts: { ...config.tts, [key]: value } });
    };

    if (loading) {
        return (
            <Box h="100vh" w="100%" bg="var(--bg-page)" display="flex" alignItems="center" justifyContent="center">
                <Spinner size="lg" color="var(--accent)" />
            </Box>
        );
    }

    if (!config) {
        return (
            <Box h="100vh" w="100%" bg="var(--bg-page)" p={6}>
                <Box maxW="4xl" mx="auto">
                    <Heading size="lg" color="var(--text-heading)" mb={4}>Settings</Heading>
                    <Box p={3} bg="var(--bg-error)" borderRadius="md">
                        <Text color="var(--text-error)" fontSize="sm">{error || 'Could not load configuration'}</Text>
                    </Box>
                </Box>
            </Box>
        );
    }

    const sb = config.sandbox;
    const sandboxType = sb.type;

    return (
        <Box h="100vh" w="100%" bg="var(--bg-page)" overflowY="auto" p={6} display="flex" flexDirection="column">
            <HStack justify="space-between" mb={6} px={2} flexShrink={0}>
                <Heading size="lg" color="var(--text-heading)">Settings</Heading>
                <Text fontSize="xs" color="var(--text-muted)" fontFamily="mono">{config.workspace}</Text>
            </HStack>

            {error && (
                <Box p={3} bg="var(--bg-error)" borderRadius="md" mb={4} mx={2}>
                    <Text color="var(--text-error)" fontSize="sm">{error}</Text>
                </Box>
            )}

            <Box
                css={{
                    columnCount: 3,
                    columnGap: '1rem',
                    '@media (max-width: 768px)': { columnCount: 1 },
                    '@media (min-width: 769px) and (max-width: 1200px)': { columnCount: 2 },
                }}
                flex={1}
            >

                {/* Sandbox */}
                <Box css={{ breakInside: 'avoid' }} mb={4}>
                    <SectionCard
                        title="Sandbox"
                        busy={savingSection === 'sandbox'}
                        onSave={() => saveSection('sandbox')}
                        status={sectionStatus.sandbox || ''}
                    >
                        <HStack gap={3} flexWrap="wrap">
                            <Box flex="1 1 100px" minW="100px">
                                <Field label="Backend">
                                    <NativeSelect.Root size="sm">
                                        <NativeSelect.Field
                                            value={sb.type}
                                            onChange={e => updateSandbox('type', e.target.value)}
                                            {...inputProps}
                                        >
                                            {SANDBOX_TYPES.map(t => (
                                                <option key={t} value={t} style={{ background: 'var(--option-bg)' }}>{t}</option>
                                            ))}
                                        </NativeSelect.Field>
                                    </NativeSelect.Root>
                                </Field>
                            </Box>
                            <Box flex="1 1 80px" minW="80px">
                                <Field label="Timeout (s)">
                                    <Input type="number" value={sb.timeout} onChange={e => updateSandbox('timeout', parseInt(e.target.value) || 120)} {...inputProps} />
                                </Field>
                            </Box>
                            <Box flex="1 1 80px" minW="80px">
                                <Field label="Memory Limit">
                                    <Input value={sb.memory_limit} onChange={e => updateSandbox('memory_limit', e.target.value)} {...inputProps} />
                                </Field>
                            </Box>
                            <Box flex="1 1 80px" minW="80px">
                                <Field label="MCP Port">
                                    <Input type="number" value={sb.mcp_port} onChange={e => updateSandbox('mcp_port', parseInt(e.target.value) || 9200)} {...inputProps} />
                                </Field>
                            </Box>
                        </HStack>

                        <HStack gap={2}>
                            <ToggleButton label="Network" value={sb.network} onChange={v => updateSandbox('network', v)} />
                            <ToggleButton label="GPU" value={sb.gpu} onChange={v => updateSandbox('gpu', v)} />
                        </HStack>

                        {(sandboxType === 'docker' || sandboxType === 'podman') && (
                            <Box p={3} bg="var(--bg-elevated)" borderRadius="md" border="1px solid" borderColor="var(--border-primary)">
                                <Text fontSize="xs" fontWeight="medium" color="var(--text-secondary)" mb={2}>Container Options</Text>
                                <HStack gap={3}>
                                    <Box flex={1}>
                                        <Field label="Image">
                                            <Input value={sb.image} onChange={e => updateSandbox('image', e.target.value)} placeholder="acai-sandbox" {...inputProps} />
                                        </Field>
                                    </Box>
                                    <Box flex={1}>
                                        <Field label="Runtime (auto if empty)">
                                            <Input value={sb.runtime} onChange={e => updateSandbox('runtime', e.target.value)} placeholder="docker / podman" {...inputProps} />
                                        </Field>
                                    </Box>
                                </HStack>
                            </Box>
                        )}

                        {sandboxType === 'firecracker' && (
                            <Box p={3} bg="var(--bg-elevated)" borderRadius="md" border="1px solid" borderColor="var(--border-primary)">
                                <Text fontSize="xs" fontWeight="medium" color="var(--text-secondary)" mb={2}>Firecracker Options</Text>
                                <VStack gap={2} align="stretch">
                                    <HStack gap={3}>
                                        <Box flex={1}>
                                            <Field label="Kernel (vmlinux)">
                                                <Input value={sb.kernel} onChange={e => updateSandbox('kernel', e.target.value)} {...inputProps} />
                                            </Field>
                                        </Box>
                                        <Box flex={1}>
                                            <Field label="Root FS (ext4)">
                                                <Input value={sb.rootfs} onChange={e => updateSandbox('rootfs', e.target.value)} {...inputProps} />
                                            </Field>
                                        </Box>
                                    </HStack>
                                    <HStack gap={3}>
                                        <Box flex={1}>
                                            <Field label="vCPUs">
                                                <Input type="number" value={sb.vcpu_count} onChange={e => updateSandbox('vcpu_count', parseInt(e.target.value) || 2)} {...inputProps} />
                                            </Field>
                                        </Box>
                                        <Box flex={1}>
                                            <Field label="Firecracker binary">
                                                <Input value={sb.firecracker_bin} onChange={e => updateSandbox('firecracker_bin', e.target.value)} placeholder="firecracker" {...inputProps} />
                                            </Field>
                                        </Box>
                                    </HStack>
                                </VStack>
                            </Box>
                        )}

                        {sandboxType === 'bubblewrap' && (
                            <Box p={3} bg="var(--bg-elevated)" borderRadius="md" border="1px solid" borderColor="var(--border-primary)">
                                <Text fontSize="xs" fontWeight="medium" color="var(--text-secondary)" mb={2}>Bubblewrap Options</Text>
                                <HStack gap={2} flexWrap="wrap">
                                    <ToggleButton label="USER" value={sb.unshare_user} onChange={v => updateSandbox('unshare_user', v)} />
                                    <ToggleButton label="PID" value={sb.unshare_pid} onChange={v => updateSandbox('unshare_pid', v)} />
                                    <ToggleButton label="IPC" value={sb.unshare_ipc} onChange={v => updateSandbox('unshare_ipc', v)} />
                                    <Box flex={1} minW="120px">
                                        <Field label="Dev mode">
                                            <NativeSelect.Root size="sm">
                                                <NativeSelect.Field value={sb.dev_mode} onChange={e => updateSandbox('dev_mode', e.target.value)} {...inputProps}>
                                                    <option value="minimal" style={{ background: 'var(--option-bg)' }}>minimal</option>
                                                    <option value="full" style={{ background: 'var(--option-bg)' }}>full</option>
                                                </NativeSelect.Field>
                                            </NativeSelect.Root>
                                        </Field>
                                    </Box>
                                </HStack>
                            </Box>
                        )}

                        {sandboxType === 'nsjail' && (
                            <Box p={3} bg="var(--bg-elevated)" borderRadius="md" border="1px solid" borderColor="var(--border-primary)">
                                <Text fontSize="xs" fontWeight="medium" color="var(--text-secondary)" mb={2}>Nsjail Options</Text>
                                <VStack gap={2} align="stretch">
                                    <Field label="Protobuf config path">
                                        <Input value={sb.nsjail_config} onChange={e => updateSandbox('nsjail_config', e.target.value)} {...inputProps} />
                                    </Field>
                                    <HStack gap={3}>
                                        <Box flex={1}>
                                            <Field label="Max PIDs">
                                                <Input type="number" value={sb.cgroup_pids_max} onChange={e => updateSandbox('cgroup_pids_max', parseInt(e.target.value) || 64)} {...inputProps} />
                                            </Field>
                                        </Box>
                                        <Box flex={1}>
                                            <Field label="rlimit_as">
                                                <Input value={sb.rlimit_as} onChange={e => updateSandbox('rlimit_as', e.target.value)} {...inputProps} />
                                            </Field>
                                        </Box>
                                    </HStack>
                                    <Field label="Seccomp policy file">
                                        <Input value={sb.seccomp_policy} onChange={e => updateSandbox('seccomp_policy', e.target.value)} {...inputProps} />
                                    </Field>
                                </VStack>
                            </Box>
                        )}
                    </SectionCard>
                </Box>

                {/* Worker */}
                <Box css={{ breakInside: 'avoid' }} mb={4}>
                    <SectionCard
                        title="Worker"
                        busy={savingSection === 'worker'}
                        onSave={() => saveSection('worker')}
                        status={sectionStatus.worker || ''}
                    >
                        <HStack gap={3}>
                            <Box flex={1}>
                                <Field label="Max Retries">
                                    <Input type="number" value={config.worker.max_retries} onChange={e => updateWorker('max_retries', parseInt(e.target.value) || 3)} {...inputProps} />
                                </Field>
                            </Box>
                            <Box flex={1}>
                                <Field label="Timeout (s)">
                                    <Input type="number" value={config.worker.timeout} onChange={e => updateWorker('timeout', parseInt(e.target.value) || 300)} {...inputProps} />
                                </Field>
                            </Box>
                            <Box flex={1}>
                                <Field label="Port">
                                    <Input type="number" value={config.worker.port} onChange={e => updateWorker('port', parseInt(e.target.value) || 5051)} {...inputProps} />
                                </Field>
                            </Box>
                        </HStack>
                        <HStack gap={3}>
                            <Box flex={1}>
                                <Field label="Host">
                                    <Input value={config.worker.host} onChange={e => updateWorker('host', e.target.value)} {...inputProps} />
                                </Field>
                            </Box>
                            <Box flex={1}>
                                <Field label="Orchestrator URL">
                                    <Input value={config.worker.orchestrator_url} onChange={e => updateWorker('orchestrator_url', e.target.value)} {...inputProps} />
                                </Field>
                            </Box>
                        </HStack>
                    </SectionCard>
                </Box>

                {/* Git */}
                <Box css={{ breakInside: 'avoid' }} mb={4}>
                    <SectionCard
                        title="Git"
                        busy={savingSection === 'git'}
                        onSave={() => saveSection('git')}
                        status={sectionStatus.git || ''}
                    >
                        <HStack gap={3}>
                            <Box flex={1}>
                                <Field label="Repo Path">
                                    <Input value={config.git.repo_path} onChange={e => updateGit('repo_path', e.target.value)} {...inputProps} />
                                </Field>
                            </Box>
                            <Box flex={1}>
                                <Field label="Worktree Dir">
                                    <Input value={config.git.worktree_dir} onChange={e => updateGit('worktree_dir', e.target.value)} {...inputProps} />
                                </Field>
                            </Box>
                        </HStack>
                    </SectionCard>
                </Box>

                {/* Queue */}
                <Box css={{ breakInside: 'avoid' }} mb={4}>
                    <SectionCard
                        title="Queue"
                        busy={savingSection === 'queue'}
                        onSave={() => saveSection('queue')}
                        status={sectionStatus.queue || ''}
                    >
                        <Field label="Database URL">
                            <Input value={config.queue.url} onChange={e => updateQueue('url', e.target.value)} {...inputProps} />
                        </Field>
                        <HStack gap={3}>
                            <Box flex={1}>
                                <Field label="Poll Interval (s)">
                                    <Input type="number" value={config.queue.poll_interval} onChange={e => updateQueue('poll_interval', parseInt(e.target.value) || 5)} {...inputProps} />
                                </Field>
                            </Box>
                            <Box flex={1}>
                                <Field label="Task Timeout (s)">
                                    <Input type="number" value={config.queue.task_timeout} onChange={e => updateQueue('task_timeout', parseInt(e.target.value) || 300)} {...inputProps} />
                                </Field>
                            </Box>
                        </HStack>
                    </SectionCard>
                </Box>

                {/* Audit */}
                <Box css={{ breakInside: 'avoid' }} mb={4}>
                    <SectionCard
                        title="Audit"
                        busy={savingSection === 'audit'}
                        onSave={() => saveSection('audit')}
                        status={sectionStatus.audit || ''}
                    >
                        <HStack gap={3}>
                            <ToggleButton label="Enabled" value={config.audit.enabled} onChange={v => updateAudit('enabled', v)} />
                            <Box flex={1}>
                                <Field label="Directory">
                                    <Input value={config.audit.dir} onChange={e => updateAudit('dir', e.target.value)} {...inputProps} />
                                </Field>
                            </Box>
                        </HStack>
                    </SectionCard>
                </Box>

                {/* CI / CD */}
                <Box css={{ breakInside: 'avoid' }} mb={4}>
                    <SectionCard
                        title="CI / CD"
                        busy={savingSection === 'ci'}
                        onSave={() => saveSection('ci')}
                        status={sectionStatus.ci || ''}
                    >
                        <HStack gap={3}>
                            <Box flex="1 1 120px" minW="120px">
                                <Field label="Platform">
                                    <NativeSelect.Root size="sm">
                                        <NativeSelect.Field
                                            value={config.ci.platform}
                                            onChange={e => updateCI('platform', e.target.value)}
                                            {...inputProps}
                                        >
                                            {['auto', 'github', 'gitlab', 'codeberg'].map(p => (
                                                <option key={p} value={p} style={{ background: 'var(--option-bg)' }}>
                                                    {p === 'auto' ? 'Auto-detect' : p.charAt(0).toUpperCase() + p.slice(1)}
                                                </option>
                                            ))}
                                        </NativeSelect.Field>
                                    </NativeSelect.Root>
                                </Field>
                            </Box>
                            <Box flex="1 1 100px" minW="100px">
                                <Field label="Default Branch">
                                    <Input
                                        value={config.ci.default_branch}
                                        onChange={e => updateCI('default_branch', e.target.value)}
                                        placeholder="main"
                                        {...inputProps}
                                    />
                                </Field>
                            </Box>
                            <Box flex="1 1 80px" minW="80px">
                                <Field label="Poll (s)">
                                    <Input
                                        type="number"
                                        value={config.ci.poll_interval}
                                        onChange={e => updateCI('poll_interval', parseInt(e.target.value) || 30)}
                                        {...inputProps}
                                    />
                                </Field>
                            </Box>
                        </HStack>

                        <HStack gap={2}>
                            <ToggleButton label="Auto-fix" value={config.ci.auto_fix} onChange={v => updateCI('auto_fix', v)} />
                        </HStack>

                        {(config.ci.platform === 'github' || config.ci.platform === 'auto') && (
                            <Box p={3} bg="var(--bg-elevated)" borderRadius="md" border="1px solid" borderColor="var(--border-primary)">
                                <Text fontSize="xs" fontWeight="medium" color="var(--text-secondary)" mb={2}>GitHub Options</Text>
                                <VStack gap={2} align="stretch">
                                    <Field label="Token (optional — leave empty to use gh CLI auth)">
                                        <Input
                                            type="password"
                                            value={config.ci.token}
                                            onChange={e => updateCI('token', e.target.value)}
                                            placeholder="ghp_..."
                                            {...inputProps}
                                        />
                                    </Field>
                                    <Text fontSize="xs" color="var(--text-muted)">
                                        The CI tools prefer the <Text as="span" fontFamily="mono" fontSize="xs">gh</Text> CLI for
                                        authentication. A personal access token is only needed if <Text as="span" fontFamily="mono" fontSize="xs">gh</Text> is
                                        not installed or not authenticated.
                                    </Text>
                                </VStack>
                            </Box>
                        )}
                    </SectionCard>
                </Box>

                {/* TTS */}
                {config.tts && (
                <Box css={{ breakInside: 'avoid' }} mb={4}>
                    <SectionCard
                        title="Text-to-Speech"
                        busy={savingSection === 'tts'}
                        onSave={() => saveSection('tts')}
                        status={sectionStatus.tts || ''}
                    >
                        <HStack gap={2}>
                            <ToggleButton label="Enabled" value={config.tts.enabled} onChange={v => updateTTS('enabled', v)} />
                            <ToggleButton label="CUDA" value={config.tts.use_cuda} onChange={v => updateTTS('use_cuda', v)} />
                        </HStack>

                        <Box>
                            <Field label="Voice">
                                <HStack gap={2}>
                                    <Box flex={1}>
                                        <NativeSelect.Root size="sm">
                                            <NativeSelect.Field
                                                value={config.tts.voice}
                                                onChange={e => updateTTS('voice', e.target.value)}
                                                {...inputProps}
                                            >
                                                {ttsVoices.length > 0 ? ttsVoices.map(v => (
                                                    <option key={v.id} value={v.id} style={{ background: 'var(--option-bg)' }}>
                                                        {v.label}{v.downloaded ? ' ✓' : ''}
                                                    </option>
                                                )) : (
                                                    <option value={config.tts.voice} style={{ background: 'var(--option-bg)' }}>
                                                        {config.tts.voice}
                                                    </option>
                                                )}
                                            </NativeSelect.Field>
                                        </NativeSelect.Root>
                                    </Box>
                                    {(() => {
                                        const sel = ttsVoices.find(v => v.id === config.tts.voice);
                                        const isDownloaded = sel?.downloaded ?? false;

                                        const startDownload = async () => {
                                            if (isDownloaded || ttsDownloading) return;
                                            setTtsDownloading(true);

                                            const voiceId = config.tts.voice;
                                            const toastId = toaster.create({
                                                type: 'loading',
                                                title: `Downloading ${voiceId}`,
                                                description: 'Starting…',
                                                duration: null as unknown as number,
                                            });

                                            try {
                                                const stream = await downloadTTSVoice(voiceId);

                                                stream.addEventListener('progress', (e: MessageEvent) => {
                                                    const p: TTSDownloadProgress = JSON.parse(e.data);
                                                    const mb = (p.received / 1_048_576).toFixed(1);
                                                    const totalMb = p.total ? (p.total / 1_048_576).toFixed(1) : '?';
                                                    toaster.update(toastId as string, {
                                                        type: 'loading',
                                                        title: `Downloading ${voiceId}`,
                                                        description: `${mb} / ${totalMb} MB  (${p.percent}%)`,
                                                    });
                                                });

                                                stream.addEventListener('done', () => {
                                                    toaster.update(toastId as string, {
                                                        type: 'success',
                                                        title: `Downloaded ${voiceId}`,
                                                        description: 'Voice model ready',
                                                        duration: 4000,
                                                    });
                                                    setTtsDownloading(false);
                                                    getTTSVoices().then(setTtsVoices).catch(() => {});
                                                });

                                                stream.addEventListener('error', (e: MessageEvent) => {
                                                    const err = JSON.parse(e.data);
                                                    toaster.update(toastId as string, {
                                                        type: 'error',
                                                        title: 'Download failed',
                                                        description: err.message || 'Unknown error',
                                                        duration: 8000,
                                                    });
                                                    setTtsDownloading(false);
                                                });

                                                stream.onerror = () => {
                                                    setTtsDownloading(false);
                                                };
                                            } catch (err) {
                                                toaster.update(toastId as string, {
                                                    type: 'error',
                                                    title: 'Download failed',
                                                    description: err instanceof Error ? err.message : 'Connection error',
                                                    duration: 8000,
                                                });
                                                setTtsDownloading(false);
                                            }
                                        };

                                        return (
                                            <Box
                                                as="button"
                                                px={3} py={1.5}
                                                borderRadius="md"
                                                fontSize="xs"
                                                fontWeight="medium"
                                                bg={isDownloaded ? 'var(--bg-elevated)' : 'var(--accent)'}
                                                color={isDownloaded ? 'var(--text-muted)' : 'var(--text-inverse)'}
                                                cursor={isDownloaded || ttsDownloading ? 'default' : 'pointer'}
                                                border="1px solid"
                                                borderColor={isDownloaded ? 'var(--border-primary)' : 'transparent'}
                                                onClick={startDownload}
                                                _hover={isDownloaded || ttsDownloading ? {} : { bg: 'var(--accent-hover)' }}
                                                whiteSpace="nowrap"
                                                flexShrink={0}
                                            >
                                                {ttsDownloading ? <Spinner size="xs" /> : isDownloaded ? '✓ Ready' : 'Download'}
                                            </Box>
                                        );
                                    })()}
                                </HStack>
                            </Field>
                        </Box>

                        <Box>
                            <Field label="Model Path (optional override)">
                                <Input
                                    value={config.tts.model_path}
                                    onChange={e => updateTTS('model_path', e.target.value)}
                                    placeholder="Leave empty to use selected voice"
                                    {...inputProps}
                                />
                            </Field>
                        </Box>

                        <HStack gap={3} flexWrap="wrap">
                            <Box flex="1 1 100px" minW="100px">
                                <Field label="Speed" helperText="1 = normal, 2 = 2x faster">
                                    <Input
                                        type="number"
                                        step={0.1}
                                        min={0.1}
                                        max={5}
                                        defaultValue={config.tts.length_scale}
                                        onBlur={e => {
                                            const v = parseFloat(e.target.value);
                                            if (!isNaN(v) && v > 0) updateTTS('length_scale', v);
                                        }}
                                        {...inputProps}
                                    />
                                </Field>
                            </Box>
                            <Box flex="1 1 100px" minW="100px">
                                <Field label="Sample Rate">
                                    <Input
                                        type="number"
                                        defaultValue={config.tts.sample_rate}
                                        onBlur={e => {
                                            const v = parseInt(e.target.value);
                                            if (!isNaN(v) && v > 0) updateTTS('sample_rate', v);
                                        }}
                                        {...inputProps}
                                    />
                                </Field>
                            </Box>
                            <Box flex="1 1 100px" minW="100px">
                                <Field label="Volume" helperText={`${Math.round((config.tts.volume ?? 1) * 100)}%`}>
                                    <input type="range" min={0} max={1} step={0.05}
                                        defaultValue={config.tts.volume ?? 1}
                                        onChange={e => updateTTS('volume', parseFloat(e.target.value))}
                                        style={{ width: '100%', accentColor: 'var(--accent)', cursor: 'pointer' }}
                                    />
                                </Field>
                            </Box>
                            <Box flex="1 1 100px" minW="100px">
                                <Field label="Silence (s)">
                                    <Input
                                        type="number"
                                        step={0.05}
                                        min={0}
                                        defaultValue={config.tts.sentence_silence}
                                        onBlur={e => {
                                            const v = parseFloat(e.target.value);
                                            if (!isNaN(v) && v >= 0) updateTTS('sentence_silence', v);
                                        }}
                                        {...inputProps}
                                    />
                                </Field>
                            </Box>
                        </HStack>

                        <HStack gap={3} flexWrap="wrap">
                            <Box flex="1 1 160px" minW="160px">
                                <Field label="Sentence End (regex)">
                                    <Input
                                        value={config.tts.sentence_end ?? '[.!?]\\s'}
                                        onChange={e => updateTTS('sentence_end', e.target.value)}
                                        placeholder={'[.!?]\\s'}
                                        {...inputProps}
                                    />
                                </Field>
                            </Box>
                            <Box flex="1 1 160px" minW="160px">
                                <Field label="Clause Break (regex)">
                                    <Input
                                        value={config.tts.clause_break ?? '[,;:\\n\\u2014]\\s'}
                                        onChange={e => updateTTS('clause_break', e.target.value)}
                                        placeholder={'[,;:\\n\\u2014]\\s'}
                                        {...inputProps}
                                    />
                                </Field>
                            </Box>
                            <Box flex="1 1 80px" minW="80px">
                                <Field label="Min Clause">
                                    <Input
                                        type="number"
                                        min={1}
                                        defaultValue={config.tts.min_clause_len ?? 40}
                                        onBlur={e => {
                                            const v = parseInt(e.target.value);
                                            if (!isNaN(v) && v > 0) updateTTS('min_clause_len', v);
                                        }}
                                        {...inputProps}
                                    />
                                </Field>
                            </Box>
                        </HStack>
                    </SectionCard>
                </Box>
                )}

                {/* Git Backup */}
                <Box css={{ breakInside: 'avoid' }} mb={4}>
                    <GitBackupSection />
                </Box>

                {/* Auto Update */}
                <Box css={{ breakInside: 'avoid' }} mb={4}>
                    <UpdateSection />
                </Box>

            </Box>
        </Box>
    );
};

// ==========================================================================
// Git Backup Section
// ==========================================================================

const ActionButton = ({ onClick, busy, children, variant = 'default' }: {
    onClick: () => void; busy: boolean; children: React.ReactNode;
    variant?: 'default' | 'accent';
}) => (
    <Box
        as="button"
        px={3} py={1.5}
        borderRadius="md"
        fontSize="sm"
        fontWeight="medium"
        border="1px solid"
        borderColor={variant === 'accent' ? 'var(--accent)' : 'var(--border-primary)'}
        bg={variant === 'accent' ? 'var(--accent)' : 'transparent'}
        color={variant === 'accent' ? 'var(--text-inverse)' : 'var(--text-secondary)'}
        cursor={busy ? 'not-allowed' : 'pointer'}
        onClick={busy ? undefined : onClick}
        _hover={{ borderColor: 'var(--accent)', bg: variant === 'accent' ? 'var(--accent-hover)' : 'var(--bg-hover)' }}
    >
        {busy ? <Spinner size="xs" /> : children}
    </Box>
);

const StatusDot = ({ ok }: { ok: boolean }) => (
    <Box
        w="8px" h="8px"
        borderRadius="full"
        bg={ok ? '#48bb78' : 'var(--text-muted)'}
        flexShrink={0}
    />
);

function _deployKeyUrl(remote: string): string {
    let owner = '', repo = '';
    const sshMatch = remote.match(/github\.com[:\-]([^/]+)\/(.+?)(?:\.git)?$/);
    if (sshMatch) { owner = sshMatch[1]; repo = sshMatch[2]; }
    const httpsMatch = remote.match(/github\.com\/([^/]+)\/(.+?)(?:\.git)?$/);
    if (!owner && httpsMatch) { owner = httpsMatch[1]; repo = httpsMatch[2]; }
    if (owner && repo) return `https://github.com/${owner}/${repo}/settings/keys/new`;
    return 'https://github.com/settings/keys';
}

const GitBackupSection = () => {
    const [status, setStatus] = useState<GitBackupStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState('');
    const [remote, setRemote] = useState('');
    const [msg, setMsg] = useState('');
    const [testResult, setTestResult] = useState('');

    const refresh = useCallback(async () => {
        try {
            const s = await getGitBackupStatus();
            setStatus(s);
            if (s.remote) setRemote(s.remote);
        } catch { /* noop */ }
        setLoading(false);
    }, []);

    useEffect(() => { refresh(); }, [refresh]);

    const handleGenerateKey = async () => {
        setBusy('keygen');
        setMsg('');
        try {
            await generateGitKey();
            setMsg('SSH key generated');
            await refresh();
        } catch (e: any) {
            setMsg(e.message || 'Key generation failed');
        }
        setBusy('');
    };

    const handleSetup = async () => {
        if (!remote.trim()) { setMsg('Enter a remote URL'); return; }
        setBusy('setup');
        setMsg('');
        try {
            const res = await setupGitBackup(remote.trim());
            setMsg(res.push_error ? `Configured (push warning: ${res.push_error})` : 'Git backup configured');
            await refresh();
        } catch (e: any) {
            setMsg(e.message || 'Setup failed');
        }
        setBusy('');
    };

    const handleSync = async () => {
        setBusy('sync');
        setMsg('');
        try {
            const res = await triggerGitSync();
            if (res.error) setMsg(`Sync error: ${res.error}`);
            else if (res.push_error) setMsg(`Committed but push failed: ${res.push_error}`);
            else if (res.pushed) setMsg('Synced and pushed');
            else if (res.commit) setMsg(`Committed ${res.commit}`);
            else setMsg('Nothing to sync');
            await refresh();
        } catch (e: any) {
            setMsg(e.message || 'Sync failed');
        }
        setBusy('');
    };

    const handleTest = async () => {
        setBusy('test');
        setTestResult('');
        try {
            const res = await testGitConnection();
            setTestResult(res.connected ? 'Connected successfully' : `Connection failed: ${res.output}`);
        } catch (e: any) {
            setTestResult(e.message || 'Test failed');
        }
        setBusy('');
    };

    if (loading) {
        return (
            <Box p={4} bg="var(--bg-card)" borderRadius="lg" border="1px solid" borderColor="var(--border-primary)">
                <Text fontWeight="semibold" color="var(--text-heading)" fontSize="lg" mb={3}>Git Backup</Text>
                <Spinner size="sm" color="var(--accent)" />
            </Box>
        );
    }

    return (
        <Box p={4} bg="var(--bg-card)" borderRadius="lg" border="1px solid" borderColor="var(--border-primary)">
            <HStack justify="space-between" mb={3}>
                <Text fontWeight="semibold" color="var(--text-heading)" fontSize="lg">Git Backup</Text>
                <HStack gap={2}>
                    <StatusDot ok={!!status?.initialized} />
                    <Text fontSize="xs" color="var(--text-muted)">
                        {status?.initialized ? 'Initialized' : 'Not initialized'}
                    </Text>
                </HStack>
            </HStack>

            <VStack gap={3} align="stretch">
                {/* SSH Key */}
                <Box p={3} bg="var(--bg-elevated)" borderRadius="md" border="1px solid" borderColor="var(--border-primary)">
                    <Text fontSize="xs" fontWeight="medium" color="var(--text-secondary)" mb={2}>SSH Key</Text>
                    <HStack gap={2} mb={status?.ssh_public_key ? 2 : 0}>
                        <StatusDot ok={!!status?.ssh_key_exists} />
                        <Text fontSize="xs" color="var(--text-tertiary)">
                            {status?.ssh_key_exists ? 'Key exists' : 'No key generated'}
                        </Text>
                        <Box flex={1} />
                        <ActionButton onClick={handleGenerateKey} busy={busy === 'keygen'}>
                            {status?.ssh_key_exists ? 'Regenerate' : 'Generate Key'}
                        </ActionButton>
                    </HStack>
                    {status?.ssh_public_key && (
                        <Box>
                            <Box
                                p={2}
                                bg="var(--bg-input)"
                                borderRadius="md"
                                border="1px solid"
                                borderColor="var(--border-input)"
                                cursor="pointer"
                                onClick={() => navigator.clipboard.writeText(status.ssh_public_key)}
                                title="Click to copy"
                            >
                                <Text fontSize="xs" fontFamily="mono" color="var(--text-code)" wordBreak="break-all">
                                    {status.ssh_public_key}
                                </Text>
                            </Box>
                            <HStack gap={1} mt={1}>
                                <Text fontSize="xs" color="var(--text-muted)">Click to copy, then</Text>
                                {status.remote ? (
                                    <Text
                                        as="a"
                                        href={_deployKeyUrl(status.remote)}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        fontSize="xs"
                                        color="var(--text-link)"
                                        _hover={{ textDecoration: 'underline' }}
                                    >
                                        add as deploy key on GitHub
                                    </Text>
                                ) : (
                                    <Text fontSize="xs" color="var(--text-muted)">add as a deploy key on GitHub</Text>
                                )}
                            </HStack>
                        </Box>
                    )}
                </Box>

                {/* Remote setup */}
                <Field label="Remote URL (git@github.com:user/repo.git)">
                    <HStack gap={2}>
                        <Input
                            value={remote}
                            onChange={e => setRemote(e.target.value)}
                            placeholder="git@github.com:user/workspace-backup.git"
                            {...inputProps}
                            flex={1}
                        />
                        <ActionButton onClick={handleSetup} busy={busy === 'setup'} variant="accent">
                            {status?.initialized ? 'Update' : 'Setup'}
                        </ActionButton>
                    </HStack>
                </Field>

                {status?.remote && (
                    <Text fontSize="xs" color="var(--text-muted)" fontFamily="mono">
                        Remote: {status.remote}
                    </Text>
                )}

                {/* Actions */}
                <HStack gap={2} flexWrap="wrap">
                    <ActionButton onClick={handleSync} busy={busy === 'sync'} variant="accent">
                        Force Sync & Push
                    </ActionButton>
                    <ActionButton onClick={handleTest} busy={busy === 'test'}>
                        Test Connection
                    </ActionButton>
                </HStack>

                {/* Recent commits */}
                {status?.recent_commits && status.recent_commits.length > 0 && (
                    <Box p={2} bg="var(--bg-elevated)" borderRadius="md" border="1px solid" borderColor="var(--border-primary)">
                        <Text fontSize="xs" fontWeight="medium" color="var(--text-secondary)" mb={1}>Recent Commits</Text>
                        {status.recent_commits.map((c, i) => (
                            <Text key={i} fontSize="xs" fontFamily="mono" color="var(--text-tertiary)">{c}</Text>
                        ))}
                    </Box>
                )}

                {/* Last sync */}
                {status?.last_sync && (
                    <Text fontSize="xs" color="var(--text-muted)">
                        Last sync: {new Date(status.last_sync.timestamp).toLocaleString()}
                        {status.last_sync.pushed && ' — pushed'}
                        {status.last_sync.error && ` — error: ${status.last_sync.error}`}
                    </Text>
                )}

                {/* Feedback */}
                {msg && <Text fontSize="xs" color="var(--accent)">{msg}</Text>}
                {testResult && (
                    <Text fontSize="xs" color={testResult.startsWith('Connected') ? '#48bb78' : 'var(--text-error)'}>
                        {testResult}
                    </Text>
                )}
            </VStack>
        </Box>
    );
};

// ==========================================================================
// Auto-Update Section
// ==========================================================================

const UpdateSection = () => {
    const [version, setVersion] = useState<VersionInfo | null>(null);
    const [loading, setLoading] = useState(true);
    const [updating, setUpdating] = useState(false);
    const [logs, setLogs] = useState<string[]>([]);
    const [result, setResult] = useState('');
    const logEndRef = useRef<HTMLDivElement>(null);

    const refresh = useCallback(async () => {
        try {
            const v = await getVersion();
            setVersion(v);
        } catch { /* noop */ }
        setLoading(false);
    }, []);

    useEffect(() => { refresh(); }, [refresh]);

    useEffect(() => {
        logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [logs]);

    const handleCheckUpdate = async () => {
        setLoading(true);
        await refresh();
    };

    const handleUpdate = async () => {
        setUpdating(true);
        setLogs([]);
        setResult('');

        try {
            const stream = await triggerUpdate();

            stream.addEventListener('log', (e: MessageEvent) => {
                setLogs(prev => [...prev, e.data]);
            });

            stream.addEventListener('done', (e: MessageEvent) => {
                try {
                    const data = JSON.parse(e.data);
                    if (data.status === 'updated') {
                        setResult('Update complete — service is restarting...');
                    } else if (data.status === 'error') {
                        setResult(`Update failed: ${data.message || 'unknown error'}`);
                    } else {
                        setResult(JSON.stringify(data));
                    }
                } catch {
                    setResult(e.data);
                }
                setUpdating(false);
            });

            stream.onerror = (reason) => {
                setResult(reason || 'Connection lost during update');
                setUpdating(false);
            };
        } catch (e: any) {
            setResult(e.message || 'Failed to start update');
            setUpdating(false);
        }
    };

    if (loading && !version) {
        return (
            <Box p={4} bg="var(--bg-card)" borderRadius="lg" border="1px solid" borderColor="var(--border-primary)">
                <Text fontWeight="semibold" color="var(--text-heading)" fontSize="lg" mb={3}>Updates</Text>
                <Spinner size="sm" color="var(--accent)" />
            </Box>
        );
    }

    return (
        <Box p={4} bg="var(--bg-card)" borderRadius="lg" border="1px solid" borderColor="var(--border-primary)">
            <HStack justify="space-between" mb={3}>
                <Text fontWeight="semibold" color="var(--text-heading)" fontSize="lg">Updates</Text>
                {version?.update_available && (
                    <Box px={2} py={0.5} borderRadius="md" bg="var(--accent-subtle)" border="1px solid" borderColor="var(--accent)">
                        <Text fontSize="xs" fontWeight="medium" color="var(--accent)">Update available</Text>
                    </Box>
                )}
            </HStack>

            <VStack gap={3} align="stretch">
                {/* Version info */}
                <HStack gap={4}>
                    <Box>
                        <Text fontSize="xs" color="var(--text-muted)">Installed</Text>
                        <Text fontSize="md" fontWeight="semibold" fontFamily="mono" color="var(--text-primary)">
                            {version?.version || '—'}
                        </Text>
                    </Box>
                    {version?.latest && (
                        <Box>
                            <Text fontSize="xs" color="var(--text-muted)">Latest on PyPI</Text>
                            <Text
                                fontSize="md"
                                fontWeight="semibold"
                                fontFamily="mono"
                                color={version.update_available ? 'var(--accent)' : 'var(--text-primary)'}
                            >
                                {version.latest}
                            </Text>
                        </Box>
                    )}
                </HStack>

                {/* Actions */}
                <HStack gap={2}>
                    <ActionButton onClick={handleCheckUpdate} busy={loading}>
                        Check for Updates
                    </ActionButton>
                    {version?.update_available && (
                        <ActionButton onClick={handleUpdate} busy={updating} variant="accent">
                            Install Update
                        </ActionButton>
                    )}
                </HStack>

                {/* Update log */}
                {logs.length > 0 && (
                    <Box
                        p={3}
                        bg="var(--bg-elevated)"
                        borderRadius="md"
                        border="1px solid"
                        borderColor="var(--border-primary)"
                        maxH="200px"
                        overflowY="auto"
                    >
                        <Text fontSize="xs" fontWeight="medium" color="var(--text-secondary)" mb={1}>Update Log</Text>
                        {logs.map((line, i) => (
                            <Text key={i} fontSize="xs" fontFamily="mono" color="var(--text-tertiary)">{line}</Text>
                        ))}
                        <div ref={logEndRef} />
                    </Box>
                )}

                {/* Result */}
                {result && (
                    <Text
                        fontSize="xs"
                        color={result.includes('failed') || result.includes('error') || result.includes('lost')
                            ? 'var(--text-error)'
                            : 'var(--accent)'}
                    >
                        {result}
                    </Text>
                )}
            </VStack>
        </Box>
    );
};

export default SettingsPage;
