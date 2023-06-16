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
    local redir_prefix = provisioning_session_prefix.."redir-"..uuid().."/"
    dynamicredirect.set(redir_prefix, upstream_prefix)
    return redir_prefix
end
dynamicredirect.get = dynamicredirect_get

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

return dynamicredirect

-- vim:ts=8:sts=4:sw=4:expandtab:
