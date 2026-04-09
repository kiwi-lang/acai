import { HashRouter as Router, Routes, Route } from 'react-router-dom';
import {
  ChakraProvider,
  createSystem,
  defaultConfig,
} from '@chakra-ui/react';
import Layout from './layout/Layout';
import Home from './components/Home';
import ProjectsPage from './components/ProjectsPage';
import ProjectView from './components/ProjectView';
import TasksPage from './components/TasksPage';
import StatusPage from './components/StatusPage';
import { ColorModeProvider } from './components/ui/color-mode';
import { AgentSocketProvider } from './contexts/WebSocketContext';
import './App.css';

const system = createSystem(defaultConfig);

function App() {
  return (
    <ChakraProvider value={system}>
      <ColorModeProvider>
        <AgentSocketProvider>
          <Router>
            <Layout>
              <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/projects/:name" element={<ProjectView />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/status" element={<StatusPage />} />
              </Routes>
            </Layout>
          </Router>
        </AgentSocketProvider>
      </ColorModeProvider>
    </ChakraProvider>
  );
}

export default App;
