-- Access control and response rules of an M4d service location (TS 26.512 V19.3.0 clauses 7.6.4.2, 7.6.4.5 and 7.6.4.6)
--
-- The configuration of a location is generated as JSON by openresty.py and given to the functions here; it is decoded once per location.

local m4daccess = {}

local cjson = require "cjson"
local ffi = require "ffi"
local resty_sha512 = require "resty.sha512"
local resty_string = require "resty.string"

ffi.cdef[[
int inet_pton(int af, const char *src, void *dst);
]]

local configs = {}

function m4daccess.cfg(id, json)
    local c = configs[id]
    if c == nil then
        c = cjson.decode(json)
        -- JSON null is not nil
        for k, v in pairs(c) do
            if v == cjson.null then c[k] = nil end
        end
        configs[id] = c
    end
    return c
end

-- base64url (RFC 4648 section 5) without padding: the token is compared without it
local function base64url(bin)
    local s = ngx.encode_base64(bin)
    s = s:gsub("%+", "-"):gsub("/", "_"):gsub("=", "")
    return s
end

-- Clause 7.6.4.5: the query presented with a request for a resource that matches urlSignature.urlPattern is validated; every failure is a 403
function m4daccess.verify_signature(sig)
    -- an HTTP/1.0 request may have no Host header field: nginx then gives the name of the server
    local url = ngx.var.scheme .. "://" .. (ngx.var.http_host or ngx.var.host) .. (ngx.var.request_uri:match("^[^?#]*"))
    -- urlPattern is matched against this URL (scheme, authority and path, no query) and the token is computed over the whole URL (clause 7.6.4.5);
    -- TS 26.510 V19.2.0 table 8.8.3.1-1 says "the portion of the M4 request URL that matches this pattern", which the texts do not reconcile
    if not ngx.re.find(url, sig.urlPattern, "jo") then
        return true
    end
    local args = ngx.req.get_uri_args()
    local token = args[sig.tokenName]
    local expiry = args[sig.tokenExpiryName]
    -- step 1: the token is absent or malformed
    -- (RFC 4648 clause 3.2: the padding "==" of a SHA-512 value may be present or left out, clause 5 alphabet)
    if type(token) ~= "string" or not ngx.re.find(token, "^[A-Za-z0-9_-]+={0,2}$", "jo") then
        return false
    end
    if token:sub(-2) == "==" then
        token = token:sub(1, -3)
    end
    -- step 2: the expiry is absent, malformed or has passed
    if type(expiry) ~= "string" or not ngx.re.find(expiry, "^[0-9]+$", "jo") or tonumber(expiry) < ngx.time() then
        return false
    end
    -- step 3: the token is computed again
    local signed = url .. "&" .. sig.tokenExpiryName .. "=" .. expiry
    if sig.useIPAddress then
        signed = signed .. "&" .. sig.ipAddressName .. "=" .. ngx.var.remote_addr
    end
    signed = signed .. "&" .. sig.passphraseName .. "=" .. sig.passphrase
    local sha = resty_sha512:new()
    sha:update(signed)
    local expected = base64url(sha:final())
    if #expected ~= #token then
        return false
    end
    local diff = 0
    for i = 1, #expected do
        diff = bit.bor(diff, bit.bxor(expected:byte(i), token:byte(i)))
    end
    return diff == 0
end

-- Clause 7.6.4.6: access is limited to the UEs located in the zones, which the operator maps from the addresses of the UEs (a file of
-- address ranges with the country of each, for the locator type iso3166)
local ranges = nil

local function parse_address(text)
    local buf = ffi.new("uint8_t[16]")
    if ffi.C.inet_pton(2, text, buf) == 1 then
        -- IPv4 as an IPv4-mapped IPv6 address
        local mapped = ffi.new("uint8_t[16]")
        mapped[10] = 0xff
        mapped[11] = 0xff
        for i = 0, 3 do mapped[12 + i] = buf[i] end
        return mapped
    end
    if ffi.C.inet_pton(10, text, buf) == 1 then
        return buf
    end
    return nil
end

local function load_ranges(path)
    local list = {}
    local f = io.open(path, "r")
    if not f then
        return list
    end
    for line in f:lines() do
        -- the zone is an ISO 3166-1 alpha-2 country code or an ISO 3166-2 code (country code, hyphen, subdivision code of up to three characters)
        local cidr, country = line:match("^%s*([%x%.:]+/%d+)%s*[, ]%s*(%a%a%-%w%w?%w?)%s*$")
        if not cidr then
            cidr, country = line:match("^%s*([%x%.:]+/%d+)%s*[, ]%s*(%a%a)%s*$")
        end
        if cidr then
            local addr, bits = cidr:match("^(.*)/(%d+)$")
            local bin = parse_address(addr)
            if bin then
                bits = tonumber(bits)
                if not addr:find(":", 1, true) then bits = bits + 96 end
                list[#list + 1] = {bin = bin, bits = bits, country = country:upper()}
            end
        end
    end
    f:close()
    return list
end

local function in_range(bin, range)
    local full = math.floor(range.bits / 8)
    for i = 0, full - 1 do
        if bin[i] ~= range.bin[i] then return false end
    end
    local rest = range.bits % 8
    if rest > 0 then
        local mask = bit.band(bit.lshift(0xff, 8 - rest), 0xff)
        if bit.band(bin[full], mask) ~= bit.band(range.bin[full], mask) then return false end
    end
    return true
end

function m4daccess.country_of(address, path)
    if ranges == nil then
        ranges = load_ranges(path)
    end
    local bin = parse_address(address)
    if not bin then return nil end
    local best, best_bits = nil, -1
    for _, r in ipairs(ranges) do
        if r.bits > best_bits and in_range(bin, r) then
            best, best_bits = r.country, r.bits
        end
    end
    return best
end

-- The zone of a UE is what the address ranges give for its address: an ISO 3166-1 alpha-2 country code or an ISO 3166-2 code (TS 26.510 V19.2.0
-- table B.1-1). A locator gives access when it is that zone, or the country that contains a zone that is a subdivision. A locator that is a
-- subdivision does not give access to a UE whose zone is only known to be the country: the UE may be elsewhere in the country.
function m4daccess.zone_allowed(locators, zone)
    zone = zone:upper()
    local country = zone:match("^(%a%a)")
    for _, loc in ipairs(locators) do
        loc = loc:upper()
        if loc == zone or loc == country then
            return true
        end
    end
    return false
end

function m4daccess.check_geo(geo, ranges_file, address)
    local country = m4daccess.country_of(address or ngx.var.remote_addr, ranges_file)
    if not country then
        return false
    end
    return m4daccess.zone_allowed(geo.locators, country)
end

-- CORS response headers for the domain name alias of a distribution configuration (TS 26.510 table 8.8.3.1-1, domainNameAlias)
function m4daccess.cors(aliases)
    local origin = ngx.var.http_origin
    if not origin then return end
    local host = origin:match("^https?://([^/:]+)")
    if not host then return end
    for _, alias in ipairs(aliases) do
        if host:lower() == alias:lower() then
            ngx.header["Access-Control-Allow-Origin"] = origin
            ngx.header["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            ngx.header["Access-Control-Allow-Headers"] = "Range, Origin, Accept, Content-Type, CMCD-Request, CMCD-Object, CMCD-Status, CMCD-Session"
            ngx.header["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, Date"
            ngx.header["Vary"] = "Origin"
            return
        end
    end
end

-- Clause 7.6.4.2: of the header fields of the origin response only those that describe the representation, the caching directives and
-- the connection are passed on; the other origin fields are not forwarded.
-- Implementation choice, two texts conflict, see CR-038 and AS audit O-4: clause 7.6.4.2 says that no origin field is forwarded unless the flag
-- originCacheHeaders is true, but that flag is in no data model or YAML of TS 26.510 (clause 8.8.3.1), so it cannot be set. The default stands, kept to what
-- an HTTP response needs to be understood (the representation fields and the caching directives).
local keep = {
    ["content-type"] = true, ["content-length"] = true, ["content-range"] = true, ["content-encoding"] = true,
    ["content-language"] = true, ["content-disposition"] = true, ["accept-ranges"] = true, ["transfer-encoding"] = true,
    ["connection"] = true, ["keep-alive"] = true, ["date"] = true, ["server"] = true, ["age"] = true, ["vary"] = true,
    ["cache-control"] = true, ["expires"] = true, ["etag"] = true, ["last-modified"] = true, ["location"] = true,
}

function m4daccess.filter_origin_headers()
    local h = ngx.resp.get_headers(0, true)
    for name, _ in pairs(h) do
        local lname = name:lower()
        if not keep[lname] and lname:sub(1, 15) ~= "access-control-" then
            ngx.header[name] = nil
        end
    end
end

-- Implementation choice, text silent how: a caching rule with statusCodeFilters applies to the origin status codes listed; "for the others the directives
-- of the origin apply" (TS 26.512 V19.3.0 clause 7.6.4.2, TS 26.510 V19.2.0 table 8.8.3.1-1). nginx ignores or honours the cache fields of the origin for a
-- whole location, not for some codes only, so the location ignores them (the rule must override them for the codes of the filter) and an answer with another
-- code is not kept in the cache of the AS. What can be done for those answers is to hand the directives of the origin to the Media Player: this function
-- puts back the Cache-Control and Expires fields that the location hid. See AS audit O-2 (no CR: behaviour of one implementation).
-- codes: set of the origin status codes of the rule.
function m4daccess.origin_directives(codes)
    if codes[ngx.status] then
        return
    end
    local cache_control = ngx.var.upstream_http_cache_control
    if cache_control and cache_control ~= "" then
        ngx.header["Cache-Control"] = cache_control
    end
    local expires = ngx.var.upstream_http_expires
    if expires and expires ~= "" then
        ngx.header["Expires"] = expires
    end
end

-- The time-to-live signalled for a resource of a caching rule (noCache false, maxAge present), TS 26.510 V19.2.0 table 8.8.3.1-1:
-- "calculated relative to the time it was ingested by the Media AS", which TS 26.512 V19.3.0 clause 7.6.4.2 takes from the Date header
-- field of the origin response, adding that the AS "shall compensate for any synchronization skew between the origin and its own clock".
-- The compensation: the time of the ingest is read on the clock of the AS. A response that the AS fetches from the origin now was ingested
-- now, and that time is kept for the cached resource (shared dictionary m4d_ingest, key like the cache key of the resource). Only when
-- no time is kept (after a restart of nginx, or the entry was dropped) the Date of the origin response stands for it, with its skew.
-- codes: set of the origin status codes of the rule, empty for all of them. session: the provisioning session of the location.
function m4daccess.signal_ttl(max_age, codes, session)
    if next(codes) ~= nil and not codes[ngx.status] then
        return
    end
    local now = ngx.time()
    local dict = ngx.shared and ngx.shared.m4d_ingest
    local key = session and (ngx.var.m4d_key_prefix .. ":u=" .. ngx.var.m4d_path)
    local cache_status = ngx.var.upstream_cache_status
    local ingested
    if cache_status == nil or cache_status == "" or cache_status == "MISS" or cache_status == "EXPIRED" or cache_status == "BYPASS" then
        ingested = now
        if dict and key and max_age > 0 then
            dict:set(key, now, max_age)
        end
    else
        ingested = dict and key and dict:get(key)
        if not ingested then
            ingested = ngx.parse_http_time(ngx.var.upstream_http_date or "")
        end
    end
    local age = 0
    if ingested then
        age = now - ingested
    end
    if age < 0 then age = 0 end
    if age > max_age then age = max_age end
    ngx.header["Cache-Control"] = "max-age=" .. (max_age - age)
end
return m4daccess
