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
            return v,full_path:sub(k:len())
        end
    end
    return default_proxy,url_path
end
dynamicredirect.mapUrl = dynamicredirect_mapUrl

-- The URL path of a request as the origin takes it (RFC 3986 clause 3.3: the characters of a segment other than unreserved ones, sub-delimiters, ":" and
-- "@" are percent-encoded). The path of the request is decoded by the proxy: a "?" or "#" in it would else end the path of the origin URL.
function dynamicredirect.escape_path(path)
    return (path:gsub("[^A-Za-z0-9%-._~!$&'()*+,;=:@/]", function(c) return string.format("%%%02X", c:byte()) end))
end

return dynamicredirect

-- vim:ts=8:sts=4:sw=4:expandtab:
