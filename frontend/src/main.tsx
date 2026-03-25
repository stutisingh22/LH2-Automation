import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './context/AuthContext'
import { MetaProvider } from './state/MetaContext'
import { SelectionProvider } from './state/SelectionContext'
import { IntelligenceCacheProvider } from './state/IntelligenceCacheContext'
import { ApiLoadingProvider } from './context/ApiLoadingContext'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <ApiLoadingProvider>
        <AuthProvider>
          <MetaProvider>
            <SelectionProvider>
              <IntelligenceCacheProvider>
                <App />
              </IntelligenceCacheProvider>
            </SelectionProvider>
          </MetaProvider>
        </AuthProvider>
      </ApiLoadingProvider>
    </BrowserRouter>
  </StrictMode>,
)
