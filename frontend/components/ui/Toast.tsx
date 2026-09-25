"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";
import { CheckCircleIcon } from "@phosphor-icons/react";

const ToastContext = createContext<{ show: (message: string) => void }>({ show: () => {} });

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [message, setMessage] = useState("");
  const [visible, setVisible] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const show = useCallback((m: string) => {
    setMessage(m);
    setVisible(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setVisible(false);
      setMessage("");
    }, 2400);
  }, []);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed bottom-6 right-6 z-30 flex items-center gap-2 rounded-xl bg-fg px-4 py-3 text-sm text-canvas shadow-lg"
        style={{
          opacity: visible ? 1 : 0,
          transform: visible ? "none" : "translateY(12px)",
          transition: "opacity 300ms var(--ease-out), transform 300ms var(--ease-out)",
        }}
      >
        {visible && <CheckCircleIcon size={16} weight="fill" aria-hidden />}
        {message}
      </div>
    </ToastContext.Provider>
  );
}
