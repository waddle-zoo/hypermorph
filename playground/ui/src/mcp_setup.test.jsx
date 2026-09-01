import React from "react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { McpSetupWizard } from "./main.jsx";

afterEach(() => vi.unstubAllGlobals());

describe("McpSetupWizard", () => {
  it("stops before tool discovery when the entered endpoint is unreachable", async () => {
    const endpoint = "http://127.0.0.1:9/definitely-unreachable";
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    render(<McpSetupWizard />);
    fireEvent.change(screen.getByLabelText("Endpoint"), { target: { value: endpoint } });
    fireEvent.click(screen.getByRole("button", { name: "Run test" }));

    const reachable = screen.getByText("Endpoint reachable").closest(".mcp-step");
    await waitFor(() => expect(within(reachable).getByText("Failed")).toBeInTheDocument());
    expect(within(reachable).getByText(/Transport unreachable/)).toBeInTheDocument();

    const tools = screen.getByText("Tools discovered").closest(".mcp-step");
    expect(within(tools).getByText("Waiting")).toBeInTheDocument();
    expect(within(tools).queryByText("Passed")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(endpoint);
  });
});
