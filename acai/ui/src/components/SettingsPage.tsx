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
} from '../services/api';
import type { VersionInfo, GitBackupStatus } from '../services/api';
import type { SystemConfig, SandboxConfig } from '../services/types';

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

    const refresh = useCallback(() => {
        setLoading(true);
        getConfig()
            .then(c => { setConfig(c); setError(''); })
            .catch(err => setError(err instanceof Error ? err.message : 'Failed to load config'))
            .finally(() => setLoading(false));
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

            <Box display="flex" flexWrap="wrap" gap={4} flex={1} alignContent="flex-start">

                {/* Sandbox */}
                <Box flex="1 1 480px" minW="360px">
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
                <Box flex="1 1 400px" minW="320px">
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
                <Box flex="1 1 400px" minW="320px">
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
                        <ToggleButton label="Auto Commit" value={config.git.auto_commit} onChange={v => updateGit('auto_commit', v)} />
                    </SectionCard>
                </Box>

                {/* Queue */}
                <Box flex="1 1 400px" minW="320px">
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
                <Box flex="1 1 400px" minW="320px">
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

                {/* Git Backup */}
                <Box flex="1 1 480px" minW="360px">
                    <GitBackupSection />
                </Box>

                {/* Auto Update */}
                <Box flex="1 1 480px" minW="360px">
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
                            <Text fontSize="xs" color="var(--text-muted)" mt={1}>Click to copy — add this as a deploy key on GitHub</Text>
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
