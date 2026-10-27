import { activatePersona, listPersonas } from "../api/endpoints";
import type { PersonasResponse } from "../api/types";
import { getApiClient } from "./api-client";

export function loadPersonas(): Promise<PersonasResponse> {
  return getApiClient().then((client) => listPersonas(client));
}

export function switchPersona(personaId: string): Promise<PersonasResponse> {
  return getApiClient().then((client) => activatePersona(client, personaId));
}
