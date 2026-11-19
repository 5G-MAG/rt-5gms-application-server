local dynamicredirect = {}

local cjson = require "cjson"
local ngx = require "ngx"

local function uuid()
    -- Implement UUID v4 format (random)
    return string.format("%0.4x%0.4x-%0.4x-4%0.3x-%0.4x-%0.4x%0.4x%0.4x", math.random(0,0xffff), math.random(0,0xffff), math.random(0,0xffff), math.random(0,0xfff), math.random(0,0xffff), math.random(0,0xffff), math.random(0,0xffff), math.random(0,0xffff))
end

local function dynamicredirect_get(provisioning_session_prefix, upstream_prefix)
    local redir_map = ngx.shared.dynredirmap
    local _
    local key
    for _,key in ipairs(redir_map:get_keys(0)) do
        value = redir_map:get(key)
        if key:sub(0,provisioning_session_prefix:len()) == provisioning_session_prefix and value == upstream_prefix then
            dynamicredirect.set(key, value) -- Renew expiry time
            return key
        end
    end
    local redir_prefix
    for _ = 1, 16 do
        redir_prefix = provisioning_session_prefix.."redir-"..uuid().."/"
        -- add is atomic between the worker processes and fails for a name in use: an endpoint never leads to the target of another one
        local ok, err = redir_map:add(redir_prefix, upstream_prefix, 120)
        if ok or err ~= "exists" then
            if not ok then
                ngx.log(ngx.ERR, "dynamic redirect endpoint ", redir_prefix, " not stored: ", err)
            end
            return redir_prefix
        end
    end
    error("no free name for a dynamic redirect endpoint")
end
dynamicredirect.get = dynamicredirect_get

-- A worker process starts from the state of the random number generator that the master process had when it forked it, so every worker seeds it
-- again: the same names for the endpoints of different redirects would otherwise be likely (TS 26.512 V19.3.0 clause 8.2)
function dynamicredirect.seed()
    math.randomseed(ngx.time() * 1000003 + ngx.worker.pid())
end
dynamicredirect.uuid = uuid

local function dynamicredirect_set(m4_prefix, upstream_prefix)
    ngx.shared.dynredirmap:set(m4_prefix, upstream_prefix, 120)
end
dynamicredirect.set = dynamicredirect_set

local function dynamicredirect_mapUrl(provisioning_session_prefix, default_proxy, url_path)
    local redir_map = ngx.shared.dynredirmap
    local full_path = provisioning_session_prefix..(url_path:sub(2))
    local now = ngx.time
    redir_map:flush_expired(0)
    -- ngx.log(ngx.DEBUG,"mapUrl(",provisioning_session_prefix,", ",default_proxy,", ",url_path,")")
    local map_keys = redir_map:get_keys(0)
    local _
    local k
    for _,k in ipairs(map_keys) do
        -- ngx.log(ngx.DEBUG,"k = ",k)
        -- ngx.log(ngx.DEBUG," == ",full_path:sub(0,k:len()),"?")
        if k == full_path:sub(0,k:len()) then
            local v = redir_map:get(k)
            dynamicredirect.set(k,v) -- Renew expiry time
            return v,full_path:sub(k:len()),true
        end
    end
    return default_proxy,url_path,false
end
dynamicredirect.mapUrl = dynamicredirect_mapUrl

-- The URL path of a request as the origin takes it (RFC 3986 clause 3.3: the characters of a segment other than unreserved ones, sub-delimiters, ":" and
-- "@" are percent-encoded). The path of the request is decoded by the proxy: a "?" or "#" in it would else end the path of the origin URL.
function dynamicredirect.escape_path(path)
    return (path:gsub("[^A-Za-z0-9%-._~!$&'()*+,;=:@/]", function(c) return string.format("%%%02X", c:byte()) end))
end

-- RFC 3986 clause 5.2.4
local function remove_dot_segments(path)
    local out, trailing = {}, false
    local pos = 2
    while pos <= #path + 1 do
        local stop = path:find("/", pos, true) or (#path + 1)
        local segment = path:sub(pos, stop - 1)
        trailing = false
        if segment == ".." then
            out[#out] = nil
            trailing = true
        elseif segment == "." then
            trailing = true
        else
            out[#out + 1] = segment
        end
        pos = stop + 1
    end
    if trailing and #out > 0 then
        out[#out + 1] = ""
    end
    return "/"..table.concat(out, "/")
end

-- The URL that a Location header field of the origin stands for (RFC 9110 clause 10.2.2: it may be a relative reference, resolved as of RFC 3986
-- clause 5.2.2 against the URL of the request to the origin)
function dynamicredirect.resolve(location, base)
    if location:match("^[%a][%w+.-]*:") then
        return location
    end
    local scheme, authority, path = base:match("^([%a][%w+.-]*)://([^/?#]*)([^?#]*)")
    if location:sub(1, 2) == "//" then
        return scheme..":"..location
    end
    local ref_path, tail = location:match("^([^?#]*)(.*)$")
    if ref_path == "" then
        -- (a reference without path keeps the path of the base)
    elseif ref_path:sub(1, 1) == "/" then
        path = remove_dot_segments(ref_path)
    else
        path = remove_dot_segments((path:match("^(.*/)") or "/")..ref_path)
    end
    return scheme.."://"..authority..path..tail
end

-- TS 26.512 V19.3.0 clause 8.2: the prefix of the target of a redirect of the origin that gets a dynamically generated M4d endpoint, and the rest of
-- the target (the last path segment, its query and fragment) that follows the endpoint in the location given to the Media Player
function dynamicredirect.split_location(location, base)
    local origin, path, tail = dynamicredirect.resolve(location, base):match("^([%a][%w+.-]*://[^/?#]*)([^?#]*)(.*)$")
    if path == "" then
        path = "/"
    end
    local dir, leaf = path:match("^(.*/)([^/]+/?)$")
    if not dir then
        dir, leaf = path, ""
    end
    return origin..dir, leaf..tail
end

-- The query of a request to hand to the origin or to put into the cache key: the parameters of args other than those named in drop, without empty
-- ones (what the CMCD handler of the location removed can leave a separator). The result has no question mark.
function dynamicredirect.query(args, drop)
    local kept = {}
    for param in (args or ""):gmatch("[^&]+") do
        local name = param:match("^([^=]*)")
        local dropped = false
        for _, d in ipairs(drop or {}) do
            if name == d then
                dropped = true
            end
        end
        if not dropped then
            kept[#kept + 1] = param
        end
    end
    return table.concat(kept, "&")
end

-- The query as the suffix of a URL
function dynamicredirect.suffix(query)
    if query == "" then
        return ""
    end
    return "?"..query
end

return dynamicredirect

-- vim:ts=8:sts=4:sw=4:expandtab:
