import { useState, useEffect, useCallback } from 'react';
import {
    Box, VStack, HStack, Text, Heading, Input,
    NativeSelect, Spinner,
} from '@chakra-ui/react';
import { getConfig, updateConfig } from '../services/api';
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
        <Box h="100vh" w="100%" bg="var(--bg-page)" overflowY="auto" p={6}>
            <Box maxW="4xl" mx="auto">
                <HStack justify="space-between" mb={6}>
                    <Heading size="lg" color="var(--text-heading)">Settings</Heading>
                    <Text fontSize="xs" color="var(--text-muted)" fontFamily="mono">{config.workspace}</Text>
                </HStack>

                {error && (
                    <Box p={3} bg="var(--bg-error)" borderRadius="md" mb={4}>
                        <Text color="var(--text-error)" fontSize="sm">{error}</Text>
                    </Box>
                )}

                <VStack gap={4} align="stretch">

                    {/* Sandbox */}
                    <SectionCard
                        title="Sandbox"
                        busy={savingSection === 'sandbox'}
                        onSave={() => saveSection('sandbox')}
                        status={sectionStatus.sandbox || ''}
                    >
                        <HStack gap={3}>
                            <Box flex={1}>
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
                            <Box flex={1}>
                                <Field label="Timeout (s)">
                                    <Input type="number" value={sb.timeout} onChange={e => updateSandbox('timeout', parseInt(e.target.value) || 120)} {...inputProps} />
                                </Field>
                            </Box>
                            <Box flex={1}>
                                <Field label="Memory Limit">
                                    <Input value={sb.memory_limit} onChange={e => updateSandbox('memory_limit', e.target.value)} {...inputProps} />
                                </Field>
                            </Box>
                            <Box flex={1}>
                                <Field label="MCP Port">
                                    <Input type="number" value={sb.mcp_port} onChange={e => updateSandbox('mcp_port', parseInt(e.target.value) || 9200)} {...inputProps} />
                                </Field>
                            </Box>
                        </HStack>

                        <HStack gap={2}>
                            <ToggleButton label="Network" value={sb.network} onChange={v => updateSandbox('network', v)} />
                            <ToggleButton label="GPU" value={sb.gpu} onChange={v => updateSandbox('gpu', v)} />
                        </HStack>

                        {/* Container options */}
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

                        {/* Firecracker options */}
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

                        {/* Bubblewrap options */}
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

                        {/* Nsjail options */}
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

                    {/* Worker */}
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

                    {/* Git */}
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

                    {/* Queue */}
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

                    {/* Audit */}
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

                </VStack>
            </Box>
        </Box>
    );
};

export default SettingsPage;
