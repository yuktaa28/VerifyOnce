import { useId, useState } from "react";
import { api, demoPin } from "../api";
import { useSystem } from "./status";
import { Alert, errorText } from "./ui";

const NAME_KEY = "verifyonce.officerName";

export function useOfficerName(): [string, (name: string) => void] {
  const [name, setName] = useState(() => {
    try {
      return window.localStorage.getItem(NAME_KEY) ?? "";
    } catch {
      return "";
    }
  });
  const save = (value: string) => {
    setName(value);
    try {
      window.localStorage.setItem(NAME_KEY, value);
    } catch {
      /* not remembered */
    }
  };
  return [name, save];
}

/** Officer name (and, on the public demo, the demo PIN) used for every officer action. */
export function OfficerIdentity({ name, onName }: { name: string; onName: (v: string) => void }) {
  const { status } = useSystem();
  const [pin, setPin] = useState(demoPin.get());
  const [pinState, setPinState] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const ids = { name: useId(), pin: useId() };

  const checkPin = async () => {
    demoPin.set(pin.trim());
    try {
      await api.checkPin();
      setPinState({ tone: "success", text: "PIN accepted for this browser tab." });
    } catch (exc) {
      setPinState({ tone: "error", text: errorText(exc) });
    }
  };

  return (
    <div className="card grid gap-4 p-4 sm:grid-cols-2 sm:p-5">
      <div>
        <label htmlFor={ids.name} className="field-label">
          Officer name
        </label>
        <input
          id={ids.name}
          className="input"
          value={name}
          placeholder="Demo Officer"
          autoComplete="off"
          onChange={(e) => onName(e.target.value)}
        />
        <p className="mt-1 text-xs text-muted">Recorded on every officer action. This demo has no login.</p>
      </div>
      {status?.officer_pin_required ? (
        <div>
          {status.officer_actions_enabled ? (
            <>
              <label htmlFor={ids.pin} className="field-label">
                Demo PIN
              </label>
              <div className="flex gap-2">
                <input
                  id={ids.pin}
                  type="password"
                  className="input"
                  value={pin}
                  autoComplete="off"
                  onChange={(e) => {
                    setPin(e.target.value);
                    setPinState(null);
                  }}
                />
                <button type="button" className="btn btn-secondary shrink-0" onClick={() => void checkPin()}>
                  Use PIN
                </button>
              </div>
              <p className="mt-1 text-xs text-muted">Officer actions on the public demo need the PIN. Viewing does not.</p>
              {pinState ? (
                <div className="mt-2">
                  <Alert tone={pinState.tone}>{pinState.text}</Alert>
                </div>
              ) : null}
            </>
          ) : (
            <Alert tone="warning" title="Officer actions are off">
              This public demo has no PIN set, so cases can be viewed but not decided.
            </Alert>
          )}
        </div>
      ) : null}
    </div>
  );
}
