-- Hands the CMCD information of M4d requests over to the 5GMS AS process, which reports it to the 5GMSd AF at reference point M3d
-- (TS 26.512 V19.3.0 clauses 4.5.9 and 11.4.3.3; rt_5gms_as/m3d.py).
--
-- One JSON object per request on the Unix domain socket of the AS, from a timer so that the request is not held up: the location of the
-- request, its URL without query, the CMCD query argument and the CMCD request headers. Nothing else of the query is passed on (it may
-- carry a URL signature token). Nothing is sent when no socket is configured.

local cjson = require "cjson.safe"

local _M = {}

local socket_path = ""
local MAX_LINE = 8192

function _M.configure(path)
    socket_path = path or ""
end

local function send(premature, line)
    if premature then return end
    local sock = ngx.socket.tcp()
    sock:settimeout(200)
    local ok, err = sock:connect("unix:" .. socket_path)
    if not ok then
        ngx.log(ngx.WARN, "[m3d] samples socket not reachable: ", err)
        return
    end
    sock:send(line .. "\n")
    sock:close()
end

-- The line to send for the request that is being logged, or nil if it has no CMCD information or was refused
function _M.line(af_resource_id)
    if ngx.status >= 400 then return nil end
    local request_uri = ngx.var.request_uri or ""
    local path, query = request_uri:match("^([^?#]*)%??([^#]*)")
    local cmcd_query = ngx.re.match(query or "", "(?:^|&)(CMCD=[^&]*)", "jo")
    local headers, have = {}, false
    for _, name in ipairs({"CMCD-Object", "CMCD-Request", "CMCD-Status", "CMCD-Session"}) do
        local value = ngx.var["http_" .. name:lower():gsub("-", "_")]
        if value and value ~= "" then
            headers[name] = value
            have = true
        end
    end
    if not cmcd_query and not have then return nil end
    local line = cjson.encode({
        ps = af_resource_id,
        url = ngx.var.scheme .. "://" .. (ngx.var.http_host or ngx.var.host) .. path,
        query = cmcd_query and cmcd_query[1] or nil,
        headers = headers,
    })
    if not line or #line > MAX_LINE then return nil end
    return line
end

-- Called from the log phase of an M4d location
function _M.forward(af_resource_id)
    if socket_path == "" then return end
    local line = _M.line(af_resource_id)
    if line then
        local ok, err = ngx.timer.at(0, send, line)
        if not ok then
            ngx.log(ngx.WARN, "[m3d] cannot schedule the hand-over: ", err)
        end
    end
end

return _M
