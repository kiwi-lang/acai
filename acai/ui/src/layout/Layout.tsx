import { FC, ReactNode, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { IconButton, Box, VStack, HStack, Text, Button, Image } from '@chakra-ui/react';
import { ColorModeButton } from '../components/ui/color-mode';
import { useAgentSocket } from '../contexts/WebSocketContext';
import TelemetryDisplay from '../components/TelemetryDisplay';
import './Layout.css';

const HamburgerIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
    <path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z" />
  </svg>
);

const CloseIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
  </svg>
);

const PlusIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </svg>
);

const ChatIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

const TasksIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M9 11l3 3L22 4" />
    <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
  </svg>
);

const ProjectsIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
  </svg>
);

const StatusIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
  </svg>
);

const AgentsIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="8" r="4" />
    <path d="M6 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2" />
    <path d="M17 3a2 2 0 0 1 0 4" />
    <path d="M21 21v-2a4 4 0 0 0-3-3.87" />
  </svg>
);

const WorkflowIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="2" y="3" width="6" height="5" rx="1" />
    <rect x="16" y="3" width="6" height="5" rx="1" />
    <rect x="9" y="16" width="6" height="5" rx="1" />
    <path d="M8 5.5h8" />
    <path d="M5 8v5a2 2 0 002 2h2" />
    <path d="M19 8v5a2 2 0 01-2 2h-2" />
  </svg>
);

const UberIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="10" />
    <path d="M12 6v6l4 2" />
    <path d="M8 12h8" />
  </svg>
);

const HomeIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    <polyline points="9 22 9 12 15 12 15 22" />
  </svg>
);

const KnowledgeIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </svg>
);

const SkillsIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M7 5h10v2h2V3c0-1.1-.9-2-2-2H7c-1.1 0-2 .9-2 2v4h2V5z" />
    <path d="M15.41 16.59L20 12l-4.59-4.59L14 8.83 17.17 12 14 15.17l1.41 1.42z" />
    <path d="M10 15.17L6.83 12 10 8.83 8.59 7.41 4 12l4.59 4.59L10 15.17z" />
    <path d="M17 19H7v-2H5v4c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2v-4h-2v2z" />
  </svg>
);

const SettingsIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
);

const navItems = [
  { id: 'home', name: 'Home', path: '/', icon: HomeIcon, color: 'green' },
  { id: 'conversations', name: 'Conversations', path: '/conversations', icon: ChatIcon, color: 'blue' },
  { id: 'projects', name: 'Projects', path: '/projects', icon: ProjectsIcon, color: 'orange' },
  { id: 'agents', name: 'Agents', path: '/agents', icon: AgentsIcon, color: 'teal' },
  { id: 'knowledge', name: 'Knowledge', path: '/knowledge', icon: KnowledgeIcon, color: 'cyan' },
  { id: 'skills', name: 'Skills', path: '/skills', icon: SkillsIcon, color: 'yellow' },
  { id: 'tasks', name: 'Work Queue', path: '/tasks', icon: TasksIcon, color: 'blue' },
  { id: 'status', name: 'Status', path: '/status', icon: StatusIcon, color: 'purple' },
  { id: 'settings', name: 'Settings', path: '/settings', icon: SettingsIcon, color: 'gray' },
];

const devItems = [
  { id: 'workflows', name: 'Workflows', path: '/workflows', icon: WorkflowIcon, color: 'pink' },
  { id: 'uber', name: 'Uber Chat', path: '/uber', icon: UberIcon, color: 'purple' },
];

interface LayoutProps {
  children: ReactNode;
}

const Layout: FC<LayoutProps> = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const { capabilities } = useAgentSocket();

  const handleNewChat = () => {
    navigate('/');
    setIsMobileMenuOpen(false);
  };

  const closeMobileMenu = () => setIsMobileMenuOpen(false);

  return (
    <div className="layout" style={{ height: '100vh', width: '100vw', overflow: 'hidden' }}>
      {/* Mobile Menu Button */}
      <Box
        position="fixed"
        top={4}
        left={4}
        zIndex={1001}
        display={{ base: 'block', md: 'none' }}
      >
        <IconButton
          aria-label="Toggle menu"
          onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
          colorScheme="green"
          size="lg"
          borderRadius="md"
          boxShadow="lg"
        >
          {isMobileMenuOpen ? <CloseIcon /> : <HamburgerIcon />}
        </IconButton>
      </Box>

      {/* Sidebar */}
      <Box
        className={`sidebar ${isMobileMenuOpen ? 'mobile-open' : ''}`}
        w="260px"
        h="100vh"
        bg="var(--bg-sidebar)"
        color="var(--text-primary)"
        display="flex"
        flexDirection="column"
        position="fixed"
        left={0}
        top={0}
        zIndex={1000}
        transition="transform 0.3s"
        transform={{ base: isMobileMenuOpen ? 'translateX(0)' : 'translateX(-100%)', md: 'translateX(0)' }}
        borderRight="1px solid"
        borderColor="var(--border-primary)"
      >
        {/* Header */}
        <Box p={4} borderBottom="1px solid" borderColor="var(--border-primary)">
          <HStack gap={2}>
            <ColorModeButton />
            <Link to="/" style={{ textDecoration: 'none' }} onClick={closeMobileMenu}>
              <HStack gap={2}>
                <Image src="/logo192.png" alt="Açaí" w="32px" h="32px" />
                <Text fontSize="xl" fontWeight="bold" color="var(--text-heading)">
                  Açaí
                </Text>
              </HStack>
            </Link>
          </HStack>
        </Box>

        {/* New Chat Button */}
        <Box p={3}>
          <Button
            w="100%"
            onClick={handleNewChat}
            variant="outline"
            justifyContent="flex-start"
            size="md"
            color="var(--text-secondary)"
            borderColor="var(--border-secondary)"
            _hover={{ bg: 'var(--bg-hover)', borderColor: 'var(--border-primary)' }}
          >
            <HStack gap={2}>
              <PlusIcon />
              <Text>New Chat</Text>
            </HStack>
          </Button>
        </Box>

        {/* Navigation */}
        <Box px={3} pb={3} flex={1} overflowY="auto">
          <Text fontSize="xs" fontWeight="bold" color="var(--text-muted)" mb={2} px={2} textTransform="uppercase" letterSpacing="wide">
            Navigation
          </Text>
          <VStack gap={1} align="stretch">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = item.path === '/'
                ? location.pathname === '/'
                : location.pathname.startsWith(item.path);
              return (
                <Link
                  key={item.id}
                  to={item.path}
                  style={{ textDecoration: 'none', width: '100%' }}
                  onClick={closeMobileMenu}
                >
                  <HStack
                    p={2.5}
                    borderRadius="md"
                    bg={isActive ? 'var(--bg-active)' : 'transparent'}
                    _hover={{ bg: 'var(--bg-hover)' }}
                    cursor="pointer"
                    gap={3}
                    transition="all 0.2s"
                  >
                    <Box color={isActive ? `${item.color}.400` : 'var(--text-muted)'}>
                      <Icon />
                    </Box>
                    <Text
                      fontSize="sm"
                      color={isActive ? 'var(--text-heading)' : 'var(--text-secondary)'}
                      fontWeight={isActive ? 'medium' : 'normal'}
                    >
                      {item.name}
                    </Text>
                  </HStack>
                </Link>
              );
            })}
          </VStack>

          <Text fontSize="xs" fontWeight="bold" color="var(--text-muted)" mt={4} mb={2} px={2} textTransform="uppercase" letterSpacing="wide">
            Dev
          </Text>
          <VStack gap={1} align="stretch">
            {devItems.map((item) => {
              const Icon = item.icon;
              const isActive = location.pathname.startsWith(item.path);
              return (
                <Link
                  key={item.id}
                  to={item.path}
                  style={{ textDecoration: 'none', width: '100%' }}
                  onClick={closeMobileMenu}
                >
                  <HStack
                    p={2.5}
                    borderRadius="md"
                    bg={isActive ? 'var(--bg-active)' : 'transparent'}
                    _hover={{ bg: 'var(--bg-hover)' }}
                    cursor="pointer"
                    gap={3}
                    transition="all 0.2s"
                  >
                    <Box color={isActive ? `${item.color}.400` : 'var(--text-muted)'}>
                      <Icon />
                    </Box>
                    <Text
                      fontSize="sm"
                      color={isActive ? 'var(--text-heading)' : 'var(--text-secondary)'}
                      fontWeight={isActive ? 'medium' : 'normal'}
                    >
                      {item.name}
                    </Text>
                  </HStack>
                </Link>
              );
            })}
          </VStack>
        </Box>

        {/* Footer */}
        <Box p={4} borderTop="1px solid" borderColor="var(--border-primary)">
          {capabilities?.telemetry ? (
            <TelemetryDisplay />
          ) : (
            <Text fontSize="xs" color="var(--text-muted)" textAlign="center">
              Açaí v0.1.0
            </Text>
          )}
        </Box>
      </Box>

      {/* Main Content */}
      <Box
        className="main-content"
        ml={{ base: 0, md: '260px' }}
        h="100vh"
        w={{ base: '100vw', md: 'calc(100vw - 260px)' }}
        overflow="hidden"
      >
        {children}
      </Box>

      {/* Mobile Overlay */}
      {isMobileMenuOpen && (
        <Box
          position="fixed"
          top={0}
          left={0}
          right={0}
          bottom={0}
          bg="var(--bg-overlay)"
          zIndex={999}
          onClick={closeMobileMenu}
          display={{ base: 'block', md: 'none' }}
        />
      )}
    </div>
  );
};

export default Layout;
